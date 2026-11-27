"""Super admin changes to people's accounts: profiles, balances and statement lines, each written to the audit log.

Every change here goes through DynamoDB transactions, so a balance and the statement line that explains it change
together or not at all. The audit log keeps who changed what, with the item before and after:
  AUDIT#<yyyy-mm>   <iso>#<id>   admin, action, target, before, after, reason
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from boto3.dynamodb.conditions import Key

from app import ledger

# Profile fields an admin may set; the ones a sign-in also fills (name, email, phone) are then kept as the admin set them
EDITABLE = {"name", "email", "phone", "country", "currency", "frozen", "frozenReason", "kyc"}
SIGN_IN_FIELDS = {"name", "email", "phone"}
# Statement line fields an admin may change
TX_FIELDS = {"kind", "currency", "amount", "status", "note", "counterparty", "at"}
KINDS = {"topup", "payment", "send", "receive", "convert", "withdraw", "giftcard", "redeem", "refund", "bonus", "card", "adjust"}
STATUSES = {"", "pending", "paid", "failed"}


class NotFound(Exception):
    pass


def _amount(raw) -> Decimal:
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except InvalidOperation:
        raise ValueError("Enter the amount as a number, like -12.50.")
    if not value.is_finite() or abs(value) > Decimal("100000000"):
        raise ValueError("Enter a sensible amount.")
    return value


def _plain(item: dict | None) -> dict | None:
    """An item as JSON-ready values (Decimals as strings)."""
    if item is None:
        return None
    return {k: (str(v) if isinstance(v, Decimal) else _plain(v) if isinstance(v, dict) else v) for k, v in item.items()}


def _audit_item(admin: str, action: str, target: str, before=None, after=None, reason: str = "") -> dict:
    at = ledger._now()
    return {
        "pk": f"AUDIT#{at[:7]}", "sk": f"{at}#{uuid.uuid4().hex[:8]}", "at": at, "admin": admin, "action": action,
        "target": target, "before": _plain(before), "after": _plain(after), "reason": reason[:300],
    }


def audit(admin: str, action: str, target: str, before=None, after=None, reason: str = ""):
    ledger.table().put_item(Item=_audit_item(admin, action, target, before, after, reason))


def audit_log(months: int = 3, limit: int = 300) -> list[dict]:
    now = datetime.now(timezone.utc)
    found = []
    for back in range(months):
        year, month = now.year, now.month - back
        while month < 1:
            year, month = year - 1, month + 12
        found += ledger.table().query(
            KeyConditionExpression=Key("pk").eq(f"AUDIT#{year:04d}-{month:02d}"), ScanIndexForward=False, Limit=limit,
        )["Items"]
        if len(found) >= limit:
            break
    return [_plain({k: v for k, v in item.items() if k not in ("pk",)}) for item in found[:limit]]


def user_items(sub: str) -> list[dict]:
    items, start = [], {}
    while True:
        page = ledger.table().query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}"), **start)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        start = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def account(sub: str) -> dict:
    """Everything Lyrpay holds about one person: profile, every balance (zeros too) and the whole statement."""
    items = user_items(sub)
    profile = next((i for i in items if i["sk"] == "PROFILE"), None)
    if not profile:
        raise NotFound("No Lyrpay account with that id.")
    txs = sorted((i for i in items if i["sk"].startswith("TX#")), key=lambda i: i["sk"], reverse=True)
    return {
        "sub": sub,
        "profile": _plain({k: v for k, v in profile.items() if k not in ("pk", "sk", "bank")}),
        "hasBank": bool(profile.get("bank")),
        "balances": {i["currency"]: str(i["amount"]) for i in items if i["sk"].startswith("BAL#")},
        "transactions": [_plain({**{k: v for k, v in t.items() if k != "pk"}}) for t in txs],
    }


def update_profile(admin: str, sub: str, fields: dict, reason: str = "") -> dict:
    before = ledger.get_item(f"USER#{sub}", "PROFILE")
    if not before:
        raise NotFound("No Lyrpay account with that id.")
    changes = {k: v for k, v in fields.items() if k in EDITABLE}
    if not changes:
        raise ValueError("Nothing to change.")
    for key in ("country",):
        if changes.get(key):
            changes[key] = str(changes[key]).upper()
            if len(changes[key]) != 2:
                raise ValueError("Country is a two-letter code, like US.")
    if changes.get("currency"):
        changes["currency"] = str(changes["currency"]).upper()
        if len(changes["currency"]) != 3:
            raise ValueError("Currency is a three-letter code, like USD.")
    if "frozen" in changes:
        changes["frozen"] = bool(changes["frozen"])
    after = {**before, **changes}
    # Fields a sign-in would overwrite stay as the admin set them
    locked = set(before.get("adminSet") or []) | (SIGN_IN_FIELDS & changes.keys())
    if locked:
        after["adminSet"] = sorted(locked)
    writes = [ledger._put(after), ledger._put(_audit_item(admin, "profile.update", sub, before, after, reason))]
    # A new email or number is registered for payments; the old one stops finding this person
    for field in ("email", "phone"):
        old, new = (before.get(field) or "").lower(), (changes.get(field) or "").strip().lower()
        if field in changes and new != old:
            if old:
                writes.append({"Delete": {"TableName": ledger.TABLE_NAME, "Key": {"pk": f"HANDLE#{old}", "sk": "HANDLE"}}})
            if new:
                writes.append(ledger._put({"pk": f"HANDLE#{new}", "sk": "HANDLE", "sub": sub}))
    ledger._transact(writes, ValueError("Couldn't save; try again."))
    return after


def adjust(admin: str, sub: str, currency: str, amount: Decimal, note: str, allow_negative: bool = False) -> dict:
    """Add (amount > 0) or take (amount < 0) money on a balance, with a statement line the person sees."""
    if not ledger.get_item(f"USER#{sub}", "PROFILE"):
        raise NotFound("No Lyrpay account with that id.")
    if amount == 0:
        raise ValueError("Enter an amount other than zero.")
    tx = ledger._tx_item(sub, "adjust", currency, amount, note=(note or "Balance adjustment").strip()[:140])
    change = ledger._credit(sub, currency, amount) if amount > 0 or allow_negative else ledger._debit(sub, currency, -amount)
    ledger._transact(
        [change, ledger._put(tx), ledger._put(_audit_item(admin, "balance.adjust", sub, None, tx, note))],
        ledger.InsufficientFunds(),
    )
    return tx


def set_balance(admin: str, sub: str, currency: str, target: Decimal, note: str) -> dict | None:
    current = (ledger.get_item(f"USER#{sub}", f"BAL#{currency}") or {}).get("amount", Decimal(0))
    difference = target - current
    if difference == 0:
        return None
    return adjust(admin, sub, currency, difference, note or f"Balance set to {target} {currency}", allow_negative=True)


def _effect(item: dict) -> tuple[str, Decimal]:
    """What a statement line did to its balance: a failed one did nothing (it was put back)."""
    if item.get("status") == "failed":
        return item["currency"], Decimal(0)
    return item["currency"], Decimal(item["amount"])


def update_transaction(admin: str, sub: str, sk: str, fields: dict, rebalance: bool, reason: str = "") -> dict:
    """Change a statement line. With rebalance, the balance moves by the difference the change makes."""
    before = ledger.get_item(f"USER#{sub}", sk)
    if not before or not sk.startswith("TX#"):
        raise NotFound("No such statement line.")
    changes = {k: v for k, v in fields.items() if k in TX_FIELDS}
    if not changes:
        raise ValueError("Nothing to change.")
    if "kind" in changes and changes["kind"] not in KINDS:
        raise ValueError("Unknown kind.")
    if "status" in changes and changes["status"] not in STATUSES:
        raise ValueError("Status is pending, paid, failed or empty.")
    if "currency" in changes:
        changes["currency"] = str(changes["currency"]).upper()
    if "amount" in changes:
        changes["amount"] = _amount(changes["amount"])
    after = {k: v for k, v in {**before, **changes}.items() if v != ""}
    if after.get("status") != "pending":
        after.pop("queue", None)  # off the sweep's list once it isn't pending
    writes = [ledger._put(after), ledger._put(_audit_item(admin, "transaction.update", f"{sub}/{sk}", before, after, reason))]
    if rebalance:
        old_currency, old = _effect(before)
        new_currency, new = _effect(after)
        if old_currency == new_currency:
            if new != old:
                writes.append(ledger._credit(sub, new_currency, new - old))
        else:
            writes += [ledger._credit(sub, old_currency, -old), ledger._credit(sub, new_currency, new)]
    ledger._transact(writes, ValueError("The line changed meanwhile; reload and try again."))
    return after


def delete_transaction(admin: str, sub: str, sk: str, rebalance: bool, reason: str = "") -> dict:
    """Remove a statement line. With rebalance, its effect on the balance is undone too."""
    before = ledger.get_item(f"USER#{sub}", sk)
    if not before or not sk.startswith("TX#"):
        raise NotFound("No such statement line.")
    writes = [
        {"Delete": {"TableName": ledger.TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": sk}}},
        ledger._put(_audit_item(admin, "transaction.delete", f"{sub}/{sk}", before, None, reason)),
    ]
    currency, effect = _effect(before)
    if rebalance and effect:
        writes.append(ledger._credit(sub, currency, -effect))
    ledger._transact(writes, ValueError("The line changed meanwhile; reload and try again."))
    return before


def add_transaction(admin: str, sub: str, fields: dict, rebalance: bool, reason: str = "") -> dict:
    """Write a statement line by hand (for example one that went missing). With rebalance, the balance moves too."""
    if not ledger.get_item(f"USER#{sub}", "PROFILE"):
        raise NotFound("No Lyrpay account with that id.")
    kind = fields.get("kind") or "adjust"
    if kind not in KINDS:
        raise ValueError("Unknown kind.")
    currency = str(fields.get("currency") or "").upper()
    if len(currency) != 3:
        raise ValueError("Choose a currency.")
    amount = _amount(fields.get("amount") or "0")
    details = {k: str(fields[k])[:140] for k in ("note", "counterparty", "status") if fields.get(k)}
    tx = ledger._tx_item(sub, kind, currency, amount, **details)
    if fields.get("at"):
        tx["at"] = str(fields["at"])
        tx["sk"] = f"TX#{tx['at']}#{tx['id']}"
    writes = [ledger._put(tx), ledger._put(_audit_item(admin, "transaction.add", sub, None, tx, reason))]
    if rebalance and _effect(tx)[1]:
        writes.append(ledger._credit(sub, currency, _effect(tx)[1]))
    ledger._transact(writes, ValueError("Couldn't save the line; try again."))
    return tx


def delete_account(admin: str, sub: str, reason: str = "") -> int:
    """Delete everything Lyrpay holds for a person: profile, balances, statement, and the email/number that found them.
    Their Lyr sign-in (shared with Lyrtrip) is left alone. Returns how many items went."""
    items = user_items(sub)
    profile = next((i for i in items if i["sk"] == "PROFILE"), None)
    if not profile:
        raise NotFound("No Lyrpay account with that id.")
    snapshot = {"profile": profile, "balances": {i["currency"]: i["amount"] for i in items if i["sk"].startswith("BAL#")},
                "lines": sum(1 for i in items if i["sk"].startswith("TX#"))}
    audit(admin, "account.delete", sub, snapshot, None, reason)
    keys = [{"pk": i["pk"], "sk": i["sk"]} for i in items]
    for handle in (profile.get("email"), profile.get("phone")):
        if handle:
            found = ledger.get_item(f"HANDLE#{handle.lower()}", "HANDLE")
            if found and found.get("sub") == sub:
                keys.append({"pk": f"HANDLE#{handle.lower()}", "sk": "HANDLE"})
    if profile.get("referralCode"):
        keys.append({"pk": f"REF#{profile['referralCode']}", "sk": "REF"})
    with ledger.table().batch_writer() as batch:
        for key in keys:
            batch.delete_item(Key=key)
    return len(keys)


# ── Every transaction, for the super admin's Transactions panel ──────────────

def _usd(amount: Decimal, currency: str) -> Decimal:
    from app import rates
    try:
        return (amount / rates.rate("USD", currency)).quantize(Decimal("0.01"))
    except (KeyError, ArithmeticError):
        return Decimal(0)


def all_transactions(q: str = "", kind: str = "", status: str = "", app: str = "", days: int = 30, limit: int = 500) -> dict:
    """Every statement line across all accounts (people and the Lyr apps' own accounts), newest first, with who it
    belongs to and which app it came through, plus totals for what's shown. Filters: free text (name, email, tracking
    number, counterparty, note, id), kind, status ('pending', 'failed', 'done'), app ('lyrpay' or a Lyr app's id)."""
    from boto3.dynamodb.conditions import Attr
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 3650)))).isoformat(timespec="milliseconds")
    lines = ledger._scan(FilterExpression=Attr("sk").begins_with("TX#") & Attr("at").gte(since))
    people = {p["pk"]: p for p in ledger.profiles()}
    app_names = {str(m["id"]): m.get("name", "") for m in ledger._scan(FilterExpression=Attr("sk").eq("MERCHANT"))}
    q = q.strip().lower()
    q_id = ledger.tx_id_from_tracking(q) if q else None
    out, totals = [], {"count": 0, "inUsd": Decimal(0), "outUsd": Decimal(0), "pending": 0, "failed": 0}
    for tx in sorted(lines, key=lambda t: t["at"], reverse=True):
        owner = tx["pk"].removeprefix("USER#")
        profile = people.get(tx["pk"], {})
        merchant_id = owner.removeprefix("merchant_") if owner.startswith("merchant_") else ""
        via = merchant_id or str(tx.get("merchant") or "")
        if not via and tx.get("kind") == "payment":
            via = next((mid for mid, name in app_names.items() if name and name == tx.get("counterparty")), "")
        line_status = tx.get("status") or "done"
        if line_status == "paid":
            line_status = "done"
        if kind and tx.get("kind") != kind:
            continue
        if status and line_status != status:
            continue
        if app and (via or "lyrpay") != app:
            continue
        if q:
            haystack = " ".join(str(x) for x in (profile.get("name"), profile.get("email"), profile.get("phone"), tx.get("counterparty"),
                                                   tx.get("note"), tx.get("id"), tx.get("reference"), owner)).lower()
            if q not in haystack and not (q_id and tx.get("id") == q_id):
                continue
        usd = _usd(Decimal(tx["amount"]), tx["currency"])
        totals["count"] += 1
        totals["inUsd" if usd > 0 else "outUsd"] += abs(usd) if line_status != "failed" else 0
        totals["pending"] += line_status == "pending"
        totals["failed"] += line_status == "failed"
        if len(out) < limit:
            out.append({
                "sub": owner, "sk": tx["sk"], "id": tx["id"], "tracking": ledger.tracking_number(tx["id"]), "at": tx["at"],
                "kind": tx.get("kind", ""), "currency": tx["currency"], "amount": str(tx["amount"]), "usd": str(usd),
                "status": line_status, "counterparty": tx.get("counterparty", ""), "note": tx.get("note", ""),
                "rail": tx.get("rail", ""), "reference": tx.get("reference", ""),
                "name": app_names.get(merchant_id, "") if merchant_id else profile.get("name", ""),
                "email": profile.get("email", ""), "app": via or "lyrpay", "isApp": bool(merchant_id),
            })
    totals = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in totals.items()}
    return {"transactions": out, "totals": totals, "apps": [{"id": k, "name": v} for k, v in sorted(app_names.items())]}


# ── Merging two wallets of one person ────────────────────────────────────────

def merge_accounts(admin: str, source: str, target: str, reason: str) -> dict:
    """Move everything in wallet `source` into wallet `target` (the same person's two accounts), in one transaction:
    each balance and the points, with a statement line on both sides; the source's email and phone handles and every
    sign-in that reached it now reach the target; the source is frozen and marked as merged. Its history stays on it
    for the record. Refused while the source has a withdrawal or top-up on its way."""
    from boto3.dynamodb.conditions import Attr
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValueError("Say why these are the same person (at least a few words).")
    if source == target:
        raise ValueError("Pick two different accounts.")
    if source.startswith("merchant_") or target.startswith("merchant_"):
        raise ValueError("A Lyr app's own account can't be merged.")
    src, dst = ledger.get_item(f"USER#{source}", "PROFILE"), ledger.get_item(f"USER#{target}", "PROFILE")
    if not src or not dst:
        raise NotFound("No Lyrpay account with that id.")
    if src.get("mergedInto") or dst.get("mergedInto"):
        raise ValueError("One of these accounts was already merged into another.")
    if ledger.pending(source, "withdraw", 0) or ledger.pending(source, "topup", 0):
        raise ValueError("The account being merged has a withdrawal or top-up on its way. Merge once it has settled.")

    ops, moved = [], {}
    label = src.get("email") or src.get("phone") or source
    for currency, amount in ledger.balances(source).items():
        moved[currency] = str(amount)
        ops += [
            {"Update": {"TableName": ledger.TABLE_NAME, "Key": {"pk": f"USER#{source}", "sk": f"BAL#{currency}"},
                        "UpdateExpression": "SET amount = amount - :a", "ConditionExpression": "amount = :a",
                        "ExpressionAttributeValues": {":a": amount}}},
            ledger._credit(target, currency, amount),
            ledger._put(ledger._tx_item(source, "adjust", currency, -amount, note="Moved to your other Lyrpay account (accounts merged)")),
            ledger._put(ledger._tx_item(target, "adjust", currency, amount, note=f"Moved from your other Lyrpay account {label} (accounts merged)")),
        ]
    points = int(src.get("pointsBalance") or 0)
    ops += [
        {"Update": {"TableName": ledger.TABLE_NAME, "Key": {"pk": f"USER#{source}", "sk": "PROFILE"},
                    "UpdateExpression": "SET mergedInto = :t, frozen = :f, frozenReason = :r, pointsBalance = :z",
                    "ConditionExpression": "attribute_not_exists(mergedInto)",
                    "ExpressionAttributeValues": {":t": target, ":f": True, ":r": f"Merged into account {target}", ":z": 0}}},
        {"Update": {"TableName": ledger.TABLE_NAME, "Key": {"pk": f"USER#{target}", "sk": "PROFILE"},
                    "UpdateExpression": "SET pointsBalance = if_not_exists(pointsBalance, :z) + :p, mergedFrom = list_append(if_not_exists(mergedFrom, :e), :s)",
                    "ExpressionAttributeValues": {":p": points, ":z": 0, ":e": [], ":s": [source]}}},
    ]
    # Paying the source's email or phone now pays the target; its sign-ins now open the target
    for handle in {str(src.get("email") or "").lower(), str(src.get("phone") or "")} - {""}:
        owner = ledger.get_item(f"HANDLE#{handle}", "HANDLE")
        if owner and owner.get("sub") == source:
            ops.append(ledger._put({"pk": f"HANDLE#{handle}", "sk": "HANDLE", "sub": target}))
    for item in ledger._scan(FilterExpression=Attr("sk").eq("IDENTITY") & Attr("wallet").eq(source)):
        ops.append(ledger._put({**item, "wallet": target, "linkedBy": "merge", "mergedAt": ledger._now()}))
    for pool in ("lyr", "lyrcloud"):
        ops.append(ledger._put({"pk": f"IDENTITY#{pool}#{source}", "sk": "IDENTITY", "wallet": target, "pool": pool,
                                "linkedBy": "merge", "at": ledger._now()}))
    summary = {"source": source, "target": target, "balances": moved, "points": points}
    ops.append(ledger._put(_audit_item(admin, "account.merge", target, {"source": _plain(src)}, summary, reason)))
    ledger._transact(ops, ValueError("The account changed while merging. Look again and retry."))
    return summary
