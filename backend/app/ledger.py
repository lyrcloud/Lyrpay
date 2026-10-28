import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.auth import User

# One table:
#   USER#<sub>   PROFILE          name, email, phone, currency, country
#   USER#<sub>   BAL#<CUR>        amount (major units, Decimal)
#   USER#<sub>   TX#<iso>#<id>    one line of the statement
#   HANDLE#<email|phone>  HANDLE  sub, so people can be paid by email or number
#   TOPUP#<session>       TOPUP   marks a Stripe payment as credited, once
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
    return [{k: v for k, v in item.items() if k not in ("pk", "sk")} for item in items]


def find_recipient(handle: str) -> dict | None:
    item = table().get_item(Key={"pk": f"HANDLE#{handle}", "sk": "HANDLE"}).get("Item")
    if not item:
        return None
    profile = table().get_item(Key={"pk": f"USER#{item['sub']}", "sk": "PROFILE"}).get("Item") or {}
    return {"sub": item["sub"], "name": profile.get("name", "")}


def credit_topup(sub: str, session_id: str, currency: str, amount: Decimal):
    """Credit a paid Stripe Checkout session, at most once."""
    _transact([
        _put({"pk": f"TOPUP#{session_id}", "sk": "TOPUP", "sub": sub, "at": _now()}, "attribute_not_exists(pk)"),
        _credit(sub, currency, amount),
        _put(_tx_item(sub, "topup", currency, amount, reference=session_id, counterparty="Stripe")),
    ], AlreadyApplied())


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
