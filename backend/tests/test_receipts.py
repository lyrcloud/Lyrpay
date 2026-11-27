from decimal import Decimal

from boto3.dynamodb.types import TypeSerializer

from app import ledger, mail, receipts
from tests.test_api import aws  # noqa: F401  (fixture)

_serialize = TypeSerializer().serialize


def record(event, new, old=None):
    images = {"NewImage": {k: _serialize(v) for k, v in new.items()}}
    if old:
        images["OldImage"] = {k: _serialize(v) for k, v in old.items()}
    return {"eventName": event, "dynamodb": {"Keys": {"pk": {"S": new["pk"]}, "sk": {"S": new["sk"]}}, **images}}


def line(**fields):
    return {"pk": "USER#alice", "sk": "TX#2026-11-27T01:00:00.000+00:00#abc", "id": "abc", "at": "2026-11-27T01:00:00.000+00:00",
            "kind": "withdraw", "currency": "USD", "amount": Decimal("-1.94"), "counterparty": "Bank account", **fields}


def test_receipt_then_tracking_update(monkeypatch):
    ledger.table().put_item(Item={"pk": "USER#alice", "sk": "PROFILE", "name": "Alice Smith", "email": "alice@example.com"})
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html, unsubscribe="": sent.append((to, subject, text)))
    pending = line(status="pending")
    result = receipts.handler({"Records": [record("INSERT", pending)]}, None)
    assert result == {"sent": 1}
    assert sent[0][1] == "Withdrawal of $1.94 requested" and "lyrpay.com/?track=LP-ABC" in sent[0][2] and "Tracking number: LP-ABC" in sent[0][2]
    # Only a status change sends an update; another field changing doesn't
    receipts.handler({"Records": [record("MODIFY", {**pending, "attempt": 1}, pending)]}, None)
    assert len(sent) == 1
    receipts.handler({"Records": [record("MODIFY", line(status="paid"), pending)]}, None)
    assert sent[1][1] == "$1.94 is on its way to your bank"


def test_no_email_for_phone_accounts_or_unsubscribed(monkeypatch):
    ledger.table().put_item(Item={"pk": "USER#alice", "sk": "PROFILE", "name": "Alice", "email": "alice@example.com"})
    ledger.table().put_item(Item={"pk": "USER#bob", "sk": "PROFILE", "name": "Bob", "phone": "+14155552671"})
    ledger.opt_out("alice@example.com")
    sent = []
    monkeypatch.setattr(mail, "send", lambda *a, **k: sent.append(a))
    receipts.handler({"Records": [record("INSERT", line(kind="receive", amount=Decimal("5"), counterparty="Bob")),
                                  record("INSERT", {**line(kind="send"), "pk": "USER#bob"})]}, None)
    assert sent == []


def test_describes_each_kind():
    assert receipts.describe(line(kind="receive", amount=Decimal("5"), counterparty="M R S"), False)[0] == "M R S sent you $5.00"
    assert receipts.describe(line(kind="convert", amount=Decimal("-10"), toAmount=Decimal("9.2"), toCurrency="EUR"), False)[0] == \
        "You converted $10.00 to 9.20 EUR"
    assert receipts.describe(line(status="failed"), True)[0] == "Your $1.94 withdrawal came back"
