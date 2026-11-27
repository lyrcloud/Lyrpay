"""Super admin changes to people's accounts: profiles, balances and statement lines, each written to the audit log.

Every change here goes through DynamoDB transactions, so a balance and the statement line that explains it change
together or not at all. The audit log keeps who changed what, with the item before and after:
  AUDIT#<yyyy-mm>   <iso>#<id>   admin, action, target, before, after, reason
"""
import uuid
from datetime import datetime, timezone
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
