import os
from decimal import Decimal

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("MAIL_SIGNING_KEY", "test-signing-key")

from app import ledger, mail, main, rates, stripe_client  # noqa: E402
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
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
                {"AttributeName": "queue", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "pending",
                "KeySchema": [{"AttributeName": "queue", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(ledger, "_table", None)
        monkeypatch.setattr(mail, "send", lambda *args, **kwargs: None)
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


@pytest.fixture
def payouts_ready(monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    monkeypatch.setattr(stripe_client, "platform", lambda: {"country": "US", "currency": "USD"})
    monkeypatch.setattr(stripe_client, "get_account", lambda _: {"payouts_enabled": True, "capabilities": {"transfers": "active"}})
    monkeypatch.setattr(stripe_client, "create_connected_account", lambda **_: {"id": "acct_1"})
    monkeypatch.setattr(stripe_client, "onboarding_link", lambda account, origin: f"https://connect.stripe.com/{account}")
    monkeypatch.setattr(stripe_client, "find_transfer", lambda account, reference: None)


def test_withdraw_after_onboarding(as_user, payouts_ready, monkeypatch):
    sent = []
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *args: sent.append(args) or {"id": "tr_1"})
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_w1"})
    assert client.post("/api/payouts/onboard").status_code == 400  # needs a country
    client.patch("/api/me", json={"country": "GB"})
    assert client.post("/api/withdraw", json={"amount": "10"}).status_code == 409  # not onboarded yet

    assert client.post("/api/payouts/onboard").json()["url"].endswith("acct_1")
    assert client.get("/api/payouts").json() == {"status": "ready", "currency": "USD"}
    result = client.post("/api/withdraw", json={"amount": "10"}).json()
    assert result["balances"] == {"USD": "40.00"}
    assert result["transactions"][0]["status"] == "paid"
    assert sent == [("acct_1", Decimal("10"), "USD", result["transactions"][0]["id"])]
    assert client.post("/api/withdraw", json={"amount": "40.01"}).status_code == 409


def test_refused_withdrawal_restores_balance(as_user, payouts_ready, monkeypatch):
    def refuse(*_):
        raise stripe_client.StripeError("Insufficient platform balance.", definitive=True)
    monkeypatch.setattr(stripe_client, "transfer_to", refuse)
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_w2"})
    client.patch("/api/me", json={"country": "US"})
    client.post("/api/payouts/onboard")
    assert client.post("/api/withdraw", json={"amount": "10"}).status_code == 502
    me = client.get("/api/me").json()
    assert me["balances"] == {"USD": "50.00"}
    assert me["transactions"][0]["status"] == "failed"


def test_unanswered_withdrawal_is_settled_later(as_user, payouts_ready, monkeypatch):
    def no_answer(*_):
        raise stripe_client.StripeError("Stripe didn't answer in time.", definitive=False)
    monkeypatch.setattr(stripe_client, "transfer_to", no_answer)
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_w3"})
    client.patch("/api/me", json={"country": "US"})
    client.post("/api/payouts/onboard")
    result = client.post("/api/withdraw", json={"amount": "10"}).json()
    assert result["status"] == "pending" and result["balances"] == {"USD": "40.00"}
    tx_id = result["transactions"][0]["id"]

    # Too recent to touch: a request may still be working on it
    assert client.get("/api/me").json()["transactions"][0]["status"] == "pending"

    # Stripe did make the transfer: it's found and recorded, never sent twice
    sent = []
    monkeypatch.setattr(ledger, "pending_withdrawals", lambda sub, older_than_seconds=60: original(sub, 0))
    monkeypatch.setattr(stripe_client, "find_transfer", lambda account, reference: {"id": "tr_found"} if reference == tx_id else None)
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *args: sent.append(args) or {"id": "tr_new"})
    me = client.get("/api/me").json()
    assert me["transactions"][0]["status"] == "paid" and me["transactions"][0]["reference"] == "tr_found"
    assert sent == [] and me["balances"] == {"USD": "40.00"}


def test_unanswered_withdrawal_is_retried_when_stripe_has_none(as_user, payouts_ready, monkeypatch):
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *_: (_ for _ in ()).throw(stripe_client.StripeError("timeout", definitive=False)))
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_w4"})
    client.patch("/api/me", json={"country": "US"})
    client.post("/api/payouts/onboard")
    client.post("/api/withdraw", json={"amount": "10"})

    monkeypatch.setattr(ledger, "pending_withdrawals", lambda sub, older_than_seconds=60: original(sub, 0))
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *_: {"id": "tr_retry"})
    assert client.get("/api/me").json()["transactions"][0]["status"] == "paid"



def test_sweep_settles_withdrawals_for_people_who_never_return(as_user, payouts_ready, monkeypatch):
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *_: (_ for _ in ()).throw(stripe_client.StripeError("timeout", definitive=False)))
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_w5"})
    client.patch("/api/me", json={"country": "US"})
    client.post("/api/payouts/onboard")
    client.post("/api/withdraw", json={"amount": "10"})
    assert main.sweep_withdrawals() == {"paid": 0, "failed": 0, "pending": 0, "errors": 0}  # too recent

    monkeypatch.setattr(ledger, "all_pending_withdrawals", lambda older_than_seconds=60: original_all(0))
    assert main.sweep_withdrawals()["pending"] == 1  # Stripe still not answering: left alone
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *_: {"id": "tr_sweep"})
    assert main.sweep_withdrawals()["paid"] == 1
    assert main.sweep_withdrawals()["paid"] == 0  # settled ones leave the index
    tx = ledger.transactions("alice")[0]
    assert tx["status"] == "paid" and "queue" not in tx


original = ledger.pending_withdrawals
original_all = ledger.all_pending_withdrawals


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html, unsubscribe="": sent.append((to, subject, text)))
    monkeypatch.setattr(mail, "SEND_INTERVAL_SECONDS", 0)
    return sent


def test_welcome_email_goes_out_once(as_user, outbox):
    alice = as_user(ALICE)
    alice.get("/api/me")
    alice.get("/api/me")
    as_user(BOB).get("/api/me")  # mobile-number account with no email
    assert [(to, subject) for to, subject, _ in outbox] == [("alice@example.com", "Welcome to Lyrpay!")]
    assert "Welcome to Lyrpay, Alice!" in outbox[0][2]


def test_update_reaches_everyone_once_and_respects_unsubscribe(as_user, outbox):
    carol = User(sub="carol", email="carol@example.com", phone="", name="Carol King")
    for user in (ALICE, BOB, carol):
        as_user(user).get("/api/me")
    outbox.clear()

    preview = mail.send_update("lyrpay-live-2026-10")
    assert preview["dryRun"] and preview["emails"] == ["alice@example.com", "carol@example.com"] and not outbox

    token = mail.unsubscribe_token("carol@example.com")
    page = as_user(ALICE).get(f"/api/unsubscribe?t={token}")
    assert page.status_code == 200 and "unsubscribed" in page.text
    assert as_user(ALICE).post("/api/unsubscribe?t=forged.token").status_code == 400

    first = mail.send_update("lyrpay-live-2026-10", dry_run=False)
    again = mail.send_update("lyrpay-live-2026-10", dry_run=False)
    assert (first["sent"], again["sent"], again["skipped"]) == (1, 0, 1)
    assert [(to, subject) for to, subject, _ in outbox] == [("alice@example.com", "Lyrpay is live at lyrpay.com")]
    assert "Lyrpay is live, Alice" in outbox[0][2] and "/api/unsubscribe?t=" in outbox[0][2]


def test_failed_update_send_can_be_retried(as_user, monkeypatch):
    as_user(ALICE).get("/api/me")
    def refuse(*args, **kwargs):
        raise RuntimeError("SES is down")
    monkeypatch.setattr(mail, "send", refuse)
    monkeypatch.setattr(mail, "SEND_INTERVAL_SECONDS", 0)
    assert mail.send_update("lyrpay-live-2026-10", dry_run=False)["failed"] == 1
    monkeypatch.setattr(mail, "send", lambda *args, **kwargs: None)
    assert mail.send_update("lyrpay-live-2026-10", dry_run=False)["sent"] == 1
