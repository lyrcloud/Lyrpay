import os
from decimal import Decimal

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")

from app import ledger, main, rates, stripe_client  # noqa: E402
from app.auth import User, current_user, user_from_claims  # noqa: E402
from app.money import parse_amount, quantize  # noqa: E402

ALICE = User(sub="alice", email="alice@example.com", phone="", name="Alice")
BOB = User(sub="bob", email="", phone="+14155552671", name="Bob")


@pytest.fixture
def as_user():
    def sign_in(user):
        main.app.dependency_overrides[current_user] = lambda: user
        return TestClient(main.app)
    yield sign_in
    main.app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def aws(monkeypatch):
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName=ledger.TABLE_NAME,
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(ledger, "_table", None)
        monkeypatch.setattr(rates, "usd_rates", lambda: {"updated": "today", "rates": {"USD": 1, "EUR": 0.9, "JPY": 150}})
        yield


def paid_session(sub, amount_total=5000, currency="usd"):
    return {"client_reference_id": sub, "payment_status": "paid", "amount_total": amount_total, "currency": currency}


def test_topup_is_credited_once(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    client = as_user(ALICE)
    first = client.post("/api/topup/confirm", json={"sessionId": "cs_test_1"}).json()
    again = client.post("/api/topup/confirm", json={"sessionId": "cs_test_1"}).json()
    assert first["credited"] and not again["credited"]
    assert again["balances"] == {"USD": "50.00"}


def test_topup_for_someone_else_is_refused(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("bob"))
    response = as_user(ALICE).post("/api/topup/confirm", json={"sessionId": "cs_test_2"})
    assert response.status_code == 409


def test_unpaid_topup_is_not_credited(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: {**paid_session("alice"), "payment_status": "unpaid"})
    assert as_user(ALICE).post("/api/topup/confirm", json={"sessionId": "cs_test_3"}).status_code == 409


def test_transfer_by_phone_and_overdraft(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    as_user(BOB).get("/api/me")
    alice = as_user(ALICE)
    alice.post("/api/topup/confirm", json={"sessionId": "cs_test_4"})

    sent = alice.post("/api/transfer", json={"to": "+1 (415) 555-2671", "amount": "20", "currency": "USD"})
    assert sent.status_code == 200 and sent.json()["balances"] == {"USD": "30.00"}

    too_much = alice.post("/api/transfer", json={"to": "+14155552671", "amount": "30.01", "currency": "USD"})
    assert too_much.status_code == 409
    assert as_user(BOB).get("/api/me").json()["balances"] == {"USD": "20.00"}


def test_unknown_recipient(as_user):
    response = as_user(ALICE).post("/api/transfer", json={"to": "nobody@example.com", "amount": "1", "currency": "USD"})
    assert response.status_code == 409


def test_convert_applies_fee_and_rounds_down(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_5"})
    result = client.post("/api/convert", json={"source": "USD", "target": "JPY", "amount": "10"}).json()
    # 10 USD * 150 * 0.995 = 1492.5 -> 1492 (JPY has no minor unit)
    assert result["receive"] == "1492"
    assert result["balances"] == {"JPY": "1492", "USD": "40.00"}


def test_amount_rules():
    assert parse_amount("12.34", "USD") == Decimal("12.34")
    with pytest.raises(ValueError):
        parse_amount("1.5", "JPY")
    with pytest.raises(ValueError):
        parse_amount("-1", "USD")
    with pytest.raises(ValueError):
        parse_amount("NaN", "USD")
    assert quantize(Decimal("1.2345"), "KWD") == Decimal("1.234")


def test_phone_login_email_is_not_a_payment_handle():
    user = user_from_claims({
        "sub": "x", "email": "14155552671@phone.lyrtrip.com", "email_verified": "false",
        "phone_number": "+14155552671", "phone_number_verified": "true",
    })
    assert user.email == "" and user.phone == "+14155552671"


def test_unverified_email_is_not_a_payment_handle():
    assert user_from_claims({"sub": "x", "email": "a@b.co", "email_verified": "false"}).email == ""
    assert user_from_claims({"sub": "x", "email": "a@b.co", "identities": "[{}]"}).email == "a@b.co"


def test_requires_sign_in():
    assert TestClient(main.app).get("/api/me").status_code == 401
