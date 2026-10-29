import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app.auth import User

# One table:
#   USER#<sub>   PROFILE          name, email, phone, currency, country, payoutAccount (Stripe Connect)
#   USER#<sub>   BAL#<CUR>        amount (major units, Decimal)
#   USER#<sub>   TX#<iso>#<id>    one line of the statement
#   HANDLE#<email|phone>  HANDLE  sub, so people can be paid by email or number
#   TOPUP#<session>       TOPUP   marks a Stripe payment as credited, once
#   OPTOUT#<email>        OPTOUT  unsubscribed from Lyrpay emails
#   UPDATE#<campaign>     <email> an announcement email sent (or being sent) to that address, once
#   GIFT#<code>           GIFT    a gift card: amount, currency, status (active, redeemed, cancelled), who bought it
#   USER#<sub>   GIFTSENT#<iso>#<code>  points to a gift card the person bought
#   COUPON#<code>         COUPON  a promo code made by the admin; COUPON#<code> USED#<sub> marks a person's one use
#   REF#<code>            REF     sub, so a referral link finds who shared it
#   SETTINGS              REWARDS referral bonuses and the business conversion fee, set by the admin
# A profile can also carry referralCode, referredBy (sub), referralRewarded, and business (an application and its review).
# Pending withdrawals also carry queue="withdraw", which puts them (and only them) in the sparse
# "pending" index, so the scheduled sweep finds them without scanning the table.
TABLE_NAME = os.getenv("LEDGER_TABLE", "lyrpay-ledger")

_table = None


class InsufficientFunds(Exception):
    pass


class AlreadyApplied(Exception):
    pass


def table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return _table


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _tx_item(sub: str, kind: str, currency: str, amount: Decimal, **details) -> dict:
    at = _now()
    tx_id = uuid.uuid4().hex[:16]
    return {
        "pk": f"USER#{sub}", "sk": f"TX#{at}#{tx_id}", "id": tx_id, "at": at,
        "kind": kind, "currency": currency, "amount": amount,
        **{k: v for k, v in details.items() if v not in (None, "")},
    }


def _credit(sub: str, currency: str, amount: Decimal) -> dict:
    return {"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": f"BAL#{currency}"},
        "UpdateExpression": "ADD amount :a SET currency = :c",
        "ExpressionAttributeValues": {":a": amount, ":c": currency},
    }}


def _debit(sub: str, currency: str, amount: Decimal) -> dict:
    return {"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": f"BAL#{currency}"},
        "UpdateExpression": "SET amount = amount - :a",
        "ConditionExpression": "amount >= :a",
        "ExpressionAttributeValues": {":a": amount},
    }}


def _put(item: dict, condition: str | None = None) -> dict:
    op = {"TableName": TABLE_NAME, "Item": item}
    if condition:
        op["ConditionExpression"] = condition
    return {"Put": op}


def _transact(items: list, on_condition_fail):
    try:
        table().meta.client.transact_write_items(TransactItems=items)
    except ClientError as error:
        if error.response["Error"]["Code"] == "TransactionCanceledException":
            raise on_condition_fail
        raise


def ensure_profile(user: User) -> dict:
    """The person's profile, created on first visit, with their verified email and number registered for payments."""
    pk = f"USER#{user.sub}"
    profile = table().get_item(Key={"pk": pk, "sk": "PROFILE"}).get("Item")
    changed = not profile
    if not profile:
        profile = {"pk": pk, "sk": "PROFILE", "name": user.name, "currency": "USD", "country": "", "created": _now()}
    for field in ("name", "email", "phone"):
        value = getattr(user, field)
        if value and profile.get(field) != value:
            profile[field] = value
            changed = True
    if changed:
        table().put_item(Item=profile)
        for handle in (user.email, user.phone):
            if handle:
                table().put_item(Item={"pk": f"HANDLE#{handle}", "sk": "HANDLE", "sub": user.sub})
    return profile


def update_profile(sub: str, **fields) -> dict:
    fields = {k: v for k, v in fields.items() if v is not None}
    if fields:
        table().update_item(
            Key={"pk": f"USER#{sub}", "sk": "PROFILE"},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            ExpressionAttributeNames={f"#{k}": k for k in fields},
            ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
        )
    return table().get_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"})["Item"]


def balances(sub: str) -> dict[str, Decimal]:
    items = table().query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("BAL#"))["Items"]
    return {item["currency"]: item["amount"] for item in items if item["amount"] != 0}


def transactions(sub: str, limit: int = 50) -> list[dict]:
    items = table().query(
        KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("TX#"),
        ScanIndexForward=False, Limit=limit,
    )["Items"]
    return [{k: v for k, v in item.items() if k not in ("pk", "sk", "queue")} for item in items]


def _cutoff(older_than_seconds: int) -> str:
    return "TX#" + (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat(timespec="milliseconds")


def pending_withdrawals(sub: str, older_than_seconds: int = 60) -> list[dict]:
    """Withdrawals whose Stripe transfer had no clear answer, old enough that no request is still working on them."""
    return table().query(
        KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").between("TX#", _cutoff(older_than_seconds)),
        FilterExpression=Attr("kind").eq("withdraw") & Attr("status").eq("pending"),
    )["Items"]


def all_pending_withdrawals(older_than_seconds: int = 60) -> list[dict]:
    """Everyone's pending withdrawals, from the sparse index; the sweep re-reads each before acting."""
    items, start = [], {}
    while True:
        page = table().query(
            IndexName="pending",
            KeyConditionExpression=Key("queue").eq("withdraw") & Key("sk").lte(_cutoff(older_than_seconds)),
            **start,
        )
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        start = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def get_item(pk: str, sk: str) -> dict | None:
    return table().get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=True).get("Item")


def find_recipient(handle: str) -> dict | None:
    item = table().get_item(Key={"pk": f"HANDLE#{handle}", "sk": "HANDLE"}).get("Item")
    if not item:
        return None
    profile = table().get_item(Key={"pk": f"USER#{item['sub']}", "sk": "PROFILE"}).get("Item") or {}
    return {"sub": item["sub"], "name": profile.get("name", "")}


def credit_topup(sub: str, session_id: str, currency: str, amount: Decimal,
                 bonus: Decimal = Decimal(0), extras: list | None = None) -> bool:
    """Credit a paid Stripe Checkout session, at most once. A promo code's bonus is added to the same balance update,
    with extras (spending the code) in the same transaction; if they can't go in (the code ran out meanwhile), the
    top-up is credited without the bonus. True when the bonus went in."""
    def writes(credit: Decimal) -> list:
        return [
            _put({"pk": f"TOPUP#{session_id}", "sk": "TOPUP", "sub": sub, "at": _now()}, "attribute_not_exists(pk)"),
            _credit(sub, currency, credit),
            _put(_tx_item(sub, "topup", currency, amount, reference=session_id, counterparty="Stripe")),
        ]

    base = writes(amount)
    if extras:
        try:
            table().meta.client.transact_write_items(TransactItems=writes(amount + bonus) + extras)
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
    _transact(base, AlreadyApplied())
    return False


def convert(sub: str, source: str, amount: Decimal, target: str, received: Decimal, rate: Decimal):
    _transact([
        _debit(sub, source, amount),
        _credit(sub, target, received),
        _put(_tx_item(sub, "convert", source, -amount, toCurrency=target, toAmount=received, rate=rate)),
    ], InsufficientFunds())


def transfer(sender: User, sender_name: str, recipient: dict, currency: str, amount: Decimal, note: str):
    _transact([
        _debit(sender.sub, currency, amount),
        _credit(recipient["sub"], currency, amount),
        _put(_tx_item(sender.sub, "send", currency, -amount, counterparty=recipient["name"], note=note)),
        _put(_tx_item(recipient["sub"], "receive", currency, amount, counterparty=sender_name, note=note)),
    ], InsufficientFunds())


def bulk_transfer(sender_sub: str, sender_name: str, currency: str, payments: list[tuple[dict, Decimal, str]]):
    """Pay several people at once, all or nothing: one debit, then a credit per recipient and a line on each statement."""
    total = sum((amount for _, amount, _ in payments), Decimal(0))
    credits: dict[str, Decimal] = {}
    items = [_debit(sender_sub, currency, total)]
    for recipient, amount, note in payments:
        # One update per balance, since a transaction can't touch the same item twice
        credits[recipient["sub"]] = credits.get(recipient["sub"], Decimal(0)) + amount
        items.append(_put(_tx_item(sender_sub, "send", currency, -amount, counterparty=recipient["name"], note=note, batch=True)))
        items.append(_put(_tx_item(recipient["sub"], "receive", currency, amount, counterparty=sender_name, note=note)))
    items += [_credit(sub, currency, amount) for sub, amount in credits.items()]
    _transact(items, InsufficientFunds())


def start_withdrawal(sub: str, currency: str, amount: Decimal) -> dict:
    """Take the amount off the balance before Stripe moves it, so it can't be spent twice meanwhile."""
    tx = _tx_item(sub, "withdraw", currency, -amount, counterparty="Bank account", status="pending", queue="withdraw")
    _transact([_debit(sub, currency, amount), _put(tx)], InsufficientFunds())
    return tx


def finish_withdrawal(tx: dict, transfer_id: str):
    try:
        table().update_item(
            Key={"pk": tx["pk"], "sk": tx["sk"]},
            UpdateExpression="SET #s = :s, #r = :r REMOVE queue",
            ConditionExpression="#s = :pending",
            ExpressionAttributeNames={"#s": "status", "#r": "reference"},
            ExpressionAttributeValues={":s": "paid", ":r": transfer_id, ":pending": "pending"},
        )
    except ClientError as error:
        # Already settled by the sweep or another request
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def fail_withdrawal(tx: dict):
    """Stripe refused the transfer: put the money back on the balance."""
    sub = tx["pk"].removeprefix("USER#")
    _transact([
        _credit(sub, tx["currency"], -tx["amount"]),
        {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": tx["pk"], "sk": tx["sk"]},
            "UpdateExpression": "SET #s = :s REMOVE queue",
            "ConditionExpression": "#s = :pending",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {":s": "failed", ":pending": "pending"},
        }},
    ], AlreadyApplied())


def mark_welcomed(sub: str) -> bool:
    """Record that the welcome email is going out; False if it already went."""
    try:
        table().update_item(
            Key={"pk": f"USER#{sub}", "sk": "PROFILE"},
            UpdateExpression="SET welcomed = :at",
            ConditionExpression="attribute_exists(pk) AND attribute_not_exists(welcomed)",
            ExpressionAttributeValues={":at": _now()},
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _scan(**kwargs) -> list[dict]:
    items, start = [], {}
    while True:
        page = table().scan(**kwargs, **start)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        start = {"ExclusiveStartKey": page["LastEvaluatedKey"]}


def profiles() -> list[dict]:
    return _scan(FilterExpression=Attr("sk").eq("PROFILE"))


def opt_out(email: str):
    table().put_item(Item={"pk": f"OPTOUT#{email}", "sk": "OPTOUT", "at": _now()})


def opted_out(email: str) -> bool:
    return bool(table().get_item(Key={"pk": f"OPTOUT#{email}", "sk": "OPTOUT"}).get("Item"))


def opted_out_emails() -> set[str]:
    return {item["pk"].removeprefix("OPTOUT#") for item in _scan(FilterExpression=Attr("sk").eq("OPTOUT"))}


def claim_update(campaign: str, email: str) -> bool:
    """Reserve one send of a campaign to an address; False if it was already sent."""
    try:
        table().put_item(
            Item={"pk": f"UPDATE#{campaign}", "sk": email, "at": _now()}, ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def release_update(campaign: str, email: str):
    table().delete_item(Key={"pk": f"UPDATE#{campaign}", "sk": email})


def everything() -> dict[str, list[dict]]:
    """The whole ledger in one scan, grouped for the admin overview: profiles, balances, transactions, opt-outs."""
    groups: dict[str, list[dict]] = {
        "profiles": [], "balances": [], "transactions": [], "optouts": [], "gifts": [], "coupons": [], "cards": [],
    }
    for item in _scan():
        sk = item["sk"]
        if sk == "PROFILE":
            groups["profiles"].append(item)
        elif sk.startswith("BAL#"):
            groups["balances"].append(item)
        elif sk.startswith("CARD#"):
            groups["cards"].append(item)
        elif sk.startswith("TX#"):
            groups["transactions"].append(item)
        elif sk == "OPTOUT":
            groups["optouts"].append(item)
        elif sk == "GIFT":
            groups["gifts"].append(item)
        elif sk == "COUPON":
            groups["coupons"].append(item)
    return groups
