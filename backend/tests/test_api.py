import os
from decimal import Decimal

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_dummy")
os.environ.setdefault("MAIL_SIGNING_KEY", "test-signing-key")

from app import ledger, mail, main, plaid_client, rates, stripe_client  # noqa: E402
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
    assert client.get("/api/payouts").json() == {"status": "ready", "currency": "USD", "provider": "stripe"}
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
    monkeypatch.setattr(ledger, "pending", lambda sub, kind, older_than_seconds=60: original(sub, kind, 0))
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

    monkeypatch.setattr(ledger, "pending", lambda sub, kind, older_than_seconds=60: original(sub, kind, 0))
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

    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original_all(queue, 0))
    assert main.sweep_withdrawals()["pending"] == 1  # Stripe still not answering: left alone
    monkeypatch.setattr(stripe_client, "transfer_to", lambda *_: {"id": "tr_sweep"})
    assert main.sweep_withdrawals()["paid"] == 1
    assert main.sweep_withdrawals()["paid"] == 0  # settled ones leave the index
    tx = ledger.transactions("alice")[0]
    assert tx["status"] == "paid" and "queue" not in tx



def bank_session(sub, intent="pi_bank"):
    return {**paid_session(sub), "status": "complete", "payment_status": "unpaid", "payment_intent": intent}


def test_bank_topup_waits_then_is_credited_once(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: bank_session("alice"))
    monkeypatch.setattr(stripe_client, "get_payment_intent", lambda _: {"status": "processing"})
    client = as_user(ALICE)
    first = client.post("/api/topup/confirm", json={"sessionId": "cs_bank_1"}).json()
    client.post("/api/topup/confirm", json={"sessionId": "cs_bank_1"})  # back on the page again: still one line
    assert first["pending"] and not first["credited"] and first["balances"] == {}
    assert [(t["kind"], t["status"]) for t in first["transactions"]] == [("topup", "pending")]

    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original_all(queue, 0))
    assert main.sweep_topups()["pending"] == 1  # still on its way
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    assert main.sweep_topups()["paid"] == 1
    assert main.sweep_topups()["paid"] == 0  # settled ones leave the index
    again = client.post("/api/topup/confirm", json={"sessionId": "cs_bank_1"}).json()
    assert not again["credited"] and again["balances"] == {"USD": "50.00"}
    assert [(t["kind"], t["status"]) for t in again["transactions"]] == [("topup", "paid")]


def test_refused_bank_topup_is_marked_failed(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: bank_session("alice"))
    monkeypatch.setattr(stripe_client, "get_payment_intent", lambda _: {"status": "requires_payment_method"})
    client = as_user(ALICE)
    client.post("/api/topup/confirm", json={"sessionId": "cs_bank_2"})
    monkeypatch.setattr(ledger, "pending", lambda sub, kind, older_than_seconds=60: original(sub, kind, 0))
    me = client.get("/api/me").json()
    assert me["balances"] == {} and me["transactions"][0]["status"] == "failed"
    assert "queue" not in ledger.transactions("alice")[0]


def test_bank_topup_waits_when_stripe_wont_say(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: bank_session("alice"))
    def refused(_):
        raise stripe_client.StripeError("The provided key does not have the required permissions", definitive=True)
    monkeypatch.setattr(stripe_client, "get_payment_intent", refused)
    as_user(ALICE).post("/api/topup/confirm", json={"sessionId": "cs_bank_3"})
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original_all(queue, 0))
    assert main.sweep_topups()["pending"] == 1


original = ledger.pending
original_all = ledger.all_pending


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


ADMIN = User(sub="admin", email="lyrclouds@gmail.com", phone="", name="Lyr Admin")


def test_only_the_super_admin_sees_the_overview(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    as_user(BOB).get("/api/me")
    alice = as_user(ALICE)
    assert alice.get("/api/me").json()["admin"] is False
    alice.post("/api/topup/confirm", json={"sessionId": "cs_test_admin"})
    alice.post("/api/transfer", json={"to": "+14155552671", "amount": "10", "currency": "USD"})
    assert alice.get("/api/admin/overview").status_code == 403

    admin = as_user(ADMIN)
    assert admin.get("/api/me").json()["admin"] is True
    overview = admin.get("/api/admin/overview").json()
    assert overview["totals"]["users"] == 3
    assert overview["totals"]["heldUsd"] == 50.0
    # 50 added + 10 sent; the matching receive isn't counted twice
    assert overview["totals"]["volumeUsd"] == 60.0
    assert overview["byKind"]["receive"]["count"] == 1
    assert {u["email"] for u in overview["users"]} >= {"alice@example.com", "lyrclouds@gmail.com"}
    assert overview["recent"][0]["kind"] in ("send", "receive")


def test_unverified_admin_email_is_not_admin(as_user):
    # Without a verified email the claims give no email, so the address can't be borrowed
    claims = {"sub": "x", "email": "lyrclouds@gmail.com", "email_verified": "false"}
    assert as_user(user_from_claims(claims)).get("/api/admin/overview").status_code == 403


CAROL = User(sub="carol", email="carol@example.com", phone="", name="Carol King")


def top_up(client, monkeypatch, sub, session_id, amount_total=5000, currency="usd", coupon=""):
    session = {**paid_session(sub, amount_total, currency), "metadata": {"coupon": coupon} if coupon else {}}
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: session)
    return client.post("/api/topup/confirm", json={"sessionId": session_id}).json()


def test_gift_card_is_redeemed_once_by_anyone(as_user, monkeypatch, outbox):
    alice = as_user(ALICE)
    top_up(alice, monkeypatch, "alice", "cs_gift_1")
    bought = alice.post("/api/giftcards", json={"amount": "20", "currency": "USD", "toEmail": "carol@example.com", "toName": "Carol", "message": "Happy birthday"}).json()
    code = bought["card"]["code"]
    assert bought["balances"] == {"USD": "30.00"} and bought["emailed"]
    assert any(to == "carol@example.com" and code in text for to, _, text in outbox)

    bob = as_user(BOB)
    redeemed = bob.post("/api/giftcards/redeem", json={"code": code.lower().replace("-", "")}).json()
    assert redeemed["balances"] == {"USD": "20.00"} and redeemed["from"] == "Alice"
    assert bob.post("/api/giftcards/redeem", json={"code": code}).status_code == 409
    assert as_user(ALICE).post("/api/giftcards/cancel", json={"code": code}).status_code == 409
    # The buyer sees it as used, without the full code
    card = as_user(ALICE).get("/api/rewards").json()["giftCards"][0]
    assert card["status"] == "redeemed" and card["code"] != code


def test_unused_gift_card_can_be_cancelled_only_by_its_buyer(as_user, monkeypatch):
    alice = as_user(ALICE)
    top_up(alice, monkeypatch, "alice", "cs_gift_2")
    code = alice.post("/api/giftcards", json={"amount": "10", "currency": "USD"}).json()["card"]["code"]
    assert as_user(BOB).post("/api/giftcards/cancel", json={"code": code}).status_code == 409
    assert as_user(ALICE).post("/api/giftcards/cancel", json={"code": code}).json()["balances"] == {"USD": "50.00"}
    assert as_user(ALICE).post("/api/giftcards", json={"amount": "60", "currency": "USD"}).status_code == 409


def test_promo_code_bonus_respects_its_limits(as_user, monkeypatch):
    coupon = {"code": "launch10", "kind": "percent", "value": "10", "maxBonusUsd": "3", "maxUses": 2}
    assert as_user(ALICE).post("/api/admin/coupons", json=coupon).status_code == 403
    admin = as_user(ADMIN)
    assert admin.post("/api/admin/coupons", json=coupon).json()["coupon"]["code"] == "LAUNCH10"
    assert admin.post("/api/admin/coupons", json=coupon).status_code == 409

    alice = as_user(ALICE)
    assert alice.get("/api/coupons/check", params={"code": "launch10", "amount": "10", "currency": "USD"}).json()["bonus"] == "1.00"
    first = top_up(alice, monkeypatch, "alice", "cs_coupon_1", coupon="LAUNCH10")
    # 10% of $50 is $5, capped at $3
    assert first["bonus"] == "3.00" and first["balances"] == {"USD": "53.00"}
    again = top_up(alice, monkeypatch, "alice", "cs_coupon_2", coupon="LAUNCH10")
    assert again["bonus"] == "0.00" and "already used" in again["couponNote"]
    # Confirming the same payment twice never adds the bonus twice
    assert top_up(alice, monkeypatch, "alice", "cs_coupon_1", coupon="LAUNCH10")["balances"] == {"USD": "103.00"}

    bob = as_user(BOB)
    assert top_up(bob, monkeypatch, "bob", "cs_coupon_3", coupon="LAUNCH10")["bonus"] == "3.00"
    carol = as_user(CAROL)
    assert "fully used" in top_up(carol, monkeypatch, "carol", "cs_coupon_4", coupon="LAUNCH10")["couponNote"]

    as_user(ADMIN).patch("/api/admin/coupons/LAUNCH10", json={"active": False})
    assert as_user(CAROL).get("/api/coupons/check", params={"code": "LAUNCH10", "amount": "10", "currency": "USD"}).status_code == 400


def test_referral_pays_once_after_the_first_qualifying_top_up(as_user, monkeypatch):
    alice = as_user(ALICE)
    code = alice.get("/api/rewards").json()["referral"]["code"]
    assert alice.post("/api/referral/claim", json={"code": code}).status_code == 400

    carol = as_user(CAROL)
    assert carol.post("/api/referral/claim", json={"code": code}).json()["referrer"] == "Alice"
    assert carol.post("/api/referral/claim", json={"code": code}).status_code == 409
    # Below the $20 minimum: no bonus yet
    top_up(carol, monkeypatch, "carol", "cs_ref_1", amount_total=1000)
    assert as_user(ALICE).get("/api/me").json()["balances"] == {}
    # as_user switches who every client acts as, so sign Carol in again
    top_up(as_user(CAROL), monkeypatch, "carol", "cs_ref_2", amount_total=2500)
    top_up(as_user(CAROL), monkeypatch, "carol", "cs_ref_3", amount_total=2500)
    assert as_user(ALICE).get("/api/me").json()["balances"] == {"USD": "5.00"}
    summary = as_user(ALICE).get("/api/rewards").json()["referral"]
    assert (summary["signups"], summary["rewarded"]) == (1, 1)


def test_business_account_gets_lower_fee_and_bulk_payments(as_user, monkeypatch, outbox):
    as_user(BOB).get("/api/me")
    as_user(CAROL).get("/api/me")
    alice = as_user(ALICE)
    top_up(alice, monkeypatch, "alice", "cs_biz_1", amount_total=10000)
    application = {"company": "Alice Imports Ltd", "country": "GB", "registrationId": "GB123456", "monthlyVolume": "10k-100k"}
    assert alice.post("/api/business/apply", json={**application, "registrationId": "?"}).status_code == 400
    assert alice.post("/api/business/apply", json=application).json()["business"]["status"] == "pending"
    payroll = {"currency": "USD", "payments": [{"to": "+14155552671", "amount": "10"}, {"to": "carol@example.com", "amount": "15", "note": "Invoice 7"}]}
    assert alice.post("/api/business/pay", json=payroll).status_code == 403

    admin = as_user(ADMIN)
    assert admin.get("/api/admin/businesses").json()["businesses"][0]["company"] == "Alice Imports Ltd"
    assert admin.post("/api/admin/businesses/alice/review", json={"status": "approved"}).json()["business"]["status"] == "approved"
    assert any(to == "alice@example.com" and "approved" in subject for to, subject, _ in outbox)

    alice = as_user(ALICE)
    assert alice.get("/api/business").json()["feeBps"] == 25
    # A bad line stops the whole batch
    bad = {"currency": "USD", "payments": [*payroll["payments"], {"to": "nobody@example.com", "amount": "1"}]}
    response = alice.post("/api/business/pay", json=bad)
    assert response.status_code == 400 and "Line 3" in response.json()["detail"]
    paid = alice.post("/api/business/pay", json=payroll).json()
    assert (paid["paid"], paid["total"], paid["balances"]) == (2, "25.00", {"USD": "75.00"})
    received = as_user(CAROL).get("/api/me").json()["transactions"][0]
    assert received["counterparty"] == "Alice Imports Ltd" and received["note"] == "Invoice 7"
    too_much = {"currency": "USD", "payments": [{"to": "carol@example.com", "amount": "100"}]}
    assert as_user(ALICE).post("/api/business/pay", json=too_much).status_code == 409
    assert as_user(CAROL).get("/api/me").json()["balances"] == {"USD": "15.00"}


def test_admin_can_change_referral_and_business_settings(as_user):
    assert as_user(ALICE).put("/api/admin/settings", json={"referrerBonusUsd": "50"}).status_code == 403
    admin = as_user(ADMIN)
    assert admin.put("/api/admin/settings", json={"businessFeeBps": "500"}).status_code == 400
    saved = admin.put("/api/admin/settings", json={"referrerBonusUsd": "7.5", "businessFeeBps": "10"}).json()["settings"]
    assert saved["referrerBonusUsd"] == "7.50" and saved["businessFeeBps"] == 10


# ── Cards ────────────────────────────────────────────────────────────────────

import hashlib  # noqa: E402
import hmac  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

from app import cards  # noqa: E402

WHSEC = "whsec_test"


@pytest.fixture
def issuing(monkeypatch, as_user):
    """Issuing switched on, with Alice holding card ic_1 and $50 on her balance."""
    monkeypatch.setattr(cards, "_status", (time.time(), True))
    monkeypatch.setattr(cards, "_webhook_secret", WHSEC)
    monkeypatch.setattr(stripe_client, "platform", lambda: {"country": "US", "currency": "USD"})
    monkeypatch.setattr(stripe_client, "create_cardholder", lambda **kw: {"id": "ich_1"})
    monkeypatch.setattr(stripe_client, "create_card", lambda **kw: {"id": "ic_1", "last4": "4242", "brand": "Visa", "exp_month": 10, "exp_year": 2029, "currency": "usd", "status": "active"})
    alice = as_user(ALICE)
    alice.get("/api/me")
    alice.patch("/api/me", json={"country": "US"})
    top_up(alice, monkeypatch, "alice", "cs_card", amount_total=5000)
    application = {"firstName": "Alice", "lastName": "Smith", "dob": "1990-04-01", "line1": "1 Main St", "city": "Austin",
                   "state": "TX", "postalCode": "78701", "acceptTerms": True}
    assert alice.post("/api/cards", json=application).json()["card"]["last4"] == "4242"
    return alice


def send_event(client, kind, obj, secret=WHSEC):
    payload = json.dumps({"type": kind, "api_version": "2024-06-20", "data": {"object": obj}}).encode()
    stamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return client.post("/api/stripe/issuing", content=payload, headers={"stripe-signature": f"t={stamp},v1={signature}"})


CARD = {"id": "ic_1", "last4": "4242", "metadata": {"user": "alice"}}


def auth(auth_id, cents, status="pending"):
    return {"id": auth_id, "card": CARD, "currency": "usd", "amount": cents, "status": status, "approved": True,
            "pending_request": {"amount": cents, "currency": "usd"}, "merchant_data": {"name": "Coffee Co"}}


def balance(client):
    return client.get("/api/me").json()["balances"].get("USD")


def test_card_payment_is_held_captured_and_tipped(issuing):
    assert send_event(issuing, "issuing_authorization.request", auth("iauth_1", 2000)).json() == {"approved": True}
    assert balance(issuing) == "30.00"
    # Asked again (Stripe retries): same answer, held once
    assert send_event(issuing, "issuing_authorization.request", auth("iauth_1", 2000)).json() == {"approved": True}
    assert balance(issuing) == "30.00"
    # Captured with a tip, then closed
    txn = {"id": "ipi_1", "type": "capture", "amount": -2300, "currency": "usd", "authorization": "iauth_1", "card": "ic_1"}
    send_event(issuing, "issuing_transaction.created", txn)
    send_event(issuing, "issuing_transaction.created", txn)
    send_event(issuing, "issuing_authorization.updated", auth("iauth_1", 0, status="closed"))
    assert balance(issuing) == "27.00"
    line = issuing.get("/api/me").json()["transactions"][0]
    assert (line["kind"], line["amount"], line["status"], line["counterparty"]) == ("card", "-23.00", "paid", "Coffee Co")


def test_card_payment_over_the_balance_is_declined(issuing):
    assert send_event(issuing, "issuing_authorization.request", auth("iauth_2", 6000)).json() == {"approved": False}
    assert balance(issuing) == "50.00"


def test_reversed_hold_and_late_capture_in_any_order(issuing):
    send_event(issuing, "issuing_authorization.request", auth("iauth_3", 1500))
    send_event(issuing, "issuing_authorization.updated", auth("iauth_3", 0, status="reversed"))
    assert balance(issuing) == "50.00"
    assert issuing.get("/api/me").json()["transactions"][0]["status"] == "failed"
    # A capture that arrives after the close still charges what the merchant took
    send_event(issuing, "issuing_transaction.created", {"id": "ipi_3", "type": "capture", "amount": -1000, "currency": "usd", "authorization": "iauth_3", "card": "ic_1"})
    assert balance(issuing) == "40.00"


def test_refund_and_timeout_approval(issuing):
    send_event(issuing, "issuing_transaction.created", {"id": "ipi_r", "type": "refund", "amount": 500, "currency": "usd",
                                                         "card": {"id": "ic_1", "metadata": {"user": "alice"}}, "merchant_data": {"name": "Shop"}})
    assert balance(issuing) == "55.00"
    # Stripe approved without asking (timeout): held anyway, even past the balance
    send_event(issuing, "issuing_authorization.created", auth("iauth_t", 6000))
    assert balance(issuing) == "-5.00"


def test_card_webhook_needs_a_valid_signature(issuing):
    assert send_event(issuing, "issuing_authorization.request", auth("iauth_x", 100), secret="whsec_wrong").status_code == 400
    assert balance(issuing) == "50.00"


def test_cards_only_for_their_owner_and_once(issuing, as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "card_ephemeral_key", lambda card_id, nonce: {"secret": "ek_test"})
    monkeypatch.setattr(stripe_client, "set_card_status", lambda card_id, status: {"status": status})
    assert issuing.post("/api/cards/ic_1/reveal", json={"nonce": "n"}).json()["secret"] == "ek_test"
    assert issuing.post("/api/cards/ic_1/freeze", json={"frozen": True}).json()["card"]["status"] == "inactive"
    application = {"firstName": "Alice", "lastName": "Smith", "dob": "1990-04-01", "line1": "1 Main St", "city": "Austin", "postalCode": "78701", "acceptTerms": True}
    assert issuing.post("/api/cards", json=application).status_code == 400
    bob = as_user(BOB)
    assert bob.post("/api/cards/ic_1/reveal", json={"nonce": "n"}).status_code == 400
    assert bob.post("/api/cards/ic_1/freeze", json={"frozen": True}).status_code == 400


def test_cards_wait_for_issuing(as_user, monkeypatch):
    monkeypatch.setattr(cards, "_status", (time.time(), False))
    alice = as_user(ALICE)
    status = alice.get("/api/cards").json()
    assert status["live"] is False and status["cards"] == []
    assert alice.post("/api/cards", json={}).status_code == 409
    assert alice.post("/api/cards/waitlist").json()["waitlisted"] is True
    assert alice.get("/api/cards").json()["waitlisted"] is True


def test_crypto_markets_fall_back_to_converted_usd(monkeypatch):
    import httpx

    def fake_get(url, params):
        if params["vs_currency"] != "usd":
            return httpx.Response(400, request=httpx.Request("GET", url))
        return httpx.Response(200, request=httpx.Request("GET", url), json=[{
            "id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "image": "", "current_price": 100,
            "price_change_percentage_24h": 1.5, "market_cap": 1000, "sparkline_in_7d": {"price": [100] * 12},
        }])

    monkeypatch.setattr(rates._http, "get", fake_get)
    monkeypatch.setattr(rates, "_cache", {})
    coin = TestClient(main.app).get("/api/crypto/markets?vs=jpy").json()["coins"][0]
    assert coin["price"] == 15000 and coin["marketCap"] == 150000 and coin["sparkline"] == [15000, 15000]


def test_topup_below_stripe_minimum_is_refused(as_user, monkeypatch):
    monkeypatch.setattr(stripe_client, "create_checkout", lambda **_: {"id": "cs_test_min", "url": "https://checkout.stripe.com/x"})
    client = as_user(ALICE)
    small = client.post("/api/topup", json={"amount": "0.25", "currency": "USD"})
    assert small.status_code == 400 and "$0.50" in small.json()["detail"]
    assert "75 JPY" in client.post("/api/topup", json={"amount": "74", "currency": "JPY"}).json()["detail"]
    # The session id lets the mobile app confirm the payment itself
    assert client.post("/api/topup", json={"amount": "0.50", "currency": "USD"}).json() == {
        "url": "https://checkout.stripe.com/x", "sessionId": "cs_test_min",
    }


ADMIN = User(sub="admin", email="lyrclouds@gmail.com", phone="", name="Ada Admin")


@pytest.fixture
def plaid(monkeypatch):
    """Plaid with sandbox keys, a person who finished the Hosted Link page, and a payout Plaid has taken."""
    monkeypatch.setattr(plaid_client, "_keys", {"client_id": "c", "secret": "s", "env": "sandbox"})
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("admin"))
    monkeypatch.setattr(plaid_client, "hosted_link", lambda sub, redirect: {"link_token": "link-1", "hosted_link_url": "https://secure.plaid.com/hl/1"})
    monkeypatch.setattr(plaid_client, "linked_account", lambda token: {"public_token": "public-1", "account_id": "acc-1", "label": "Chase ••0000"} if token == "link-1" else None)
    monkeypatch.setattr(plaid_client, "exchange", lambda public: "access-1")
    state = {"payouts": [], "status": "pending"}

    def pay_out(access, account, amount, name, reference):
        state["payouts"].append((access, account, amount, name, reference))
        return {"id": "tr-1", "status": "pending"}
    monkeypatch.setattr(plaid_client, "pay_out", pay_out)
    monkeypatch.setattr(plaid_client, "get_transfer", lambda transfer_id: {"id": transfer_id, "status": state["status"]})
    return state


def linked(client):
    client.post("/api/topup/confirm", json={"sessionId": "cs_test_p1"})
    assert client.get("/api/payouts").json()["status"] == "none"
    assert client.post("/api/payouts/onboard", json={}).json() == {"url": "https://secure.plaid.com/hl/1"}
    # Back from Plaid: the next look at payouts saves the account picked there
    assert client.get("/api/payouts").json()["bank"] == "Chase ••0000"
    return client


def test_sandbox_plaid_is_only_for_admins(as_user, payouts_ready, plaid):
    assert as_user(ALICE).get("/api/payouts").json()["provider"] == "stripe"
    assert as_user(ADMIN).get("/api/payouts").json() == {"status": "none", "currency": "USD", "provider": "plaid", "bank": "", "test": True}


def test_plaid_withdrawal_is_sent_once_and_settled_when_posted(as_user, plaid, monkeypatch):
    client = as_user(ADMIN)
    assert client.post("/api/withdraw", json={"amount": "10"}).status_code == 409  # no bank yet
    linked(client)
    assert client.get("/api/payouts").json() == {"status": "ready", "currency": "USD", "provider": "plaid", "bank": "Chase ••0000", "test": True}
    profile = ledger.get_item("USER#admin", "PROFILE")
    assert profile["bank"]["access"] == "access-1" and profile["bankLink"] == ""

    result = client.post("/api/withdraw", json={"amount": "10"}).json()
    tx = result["transactions"][0]
    assert result["status"] == "pending" and result["balances"] == {"USD": "40.00"}
    assert tx["counterparty"] == "Chase ••0000" and tx["reference"] == "tr-1"
    assert plaid["payouts"] == [("access-1", "acc-1", Decimal("10"), "Ada Admin", tx["id"])]

    # Still on its way: later checks only read its status, never pay again
    monkeypatch.setattr(ledger, "pending", lambda sub, kind, older_than_seconds=60: original(sub, kind, 0))
    assert client.get("/api/me").json()["transactions"][0]["status"] == "pending"
    plaid["status"] = "posted"
    assert client.get("/api/me").json()["transactions"][0]["status"] == "paid"
    assert len(plaid["payouts"]) == 1 and ledger.balances("admin") == {"USD": Decimal("40")}


def test_refused_plaid_withdrawal_restores_balance(as_user, plaid, monkeypatch):
    def decline(*_):
        raise plaid_client.PlaidError("Plaid declined the withdrawal.", definitive=True)
    monkeypatch.setattr(plaid_client, "pay_out", decline)
    client = linked(as_user(ADMIN))
    assert client.post("/api/withdraw", json={"amount": "10"}).status_code == 502
    me = client.get("/api/me").json()
    assert me["balances"] == {"USD": "50.00"} and me["transactions"][0]["status"] == "failed"


def test_returned_plaid_withdrawal_is_restored_by_the_sweep(as_user, plaid, monkeypatch):
    client = linked(as_user(ADMIN))
    client.post("/api/withdraw", json={"amount": "10"})
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original_all(queue, 0))
    plaid["status"] = "returned"
    assert main.sweep_withdrawals()["failed"] == 1
    assert ledger.balances("admin") == {"USD": Decimal("50")}


def test_unknown_status_of_a_made_transfer_never_restores_the_balance(as_user, plaid, monkeypatch):
    client = linked(as_user(ADMIN))
    client.post("/api/withdraw", json={"amount": "10"})
    def refuse(_):
        raise plaid_client.PlaidError("INVALID_API_KEYS", definitive=True)
    monkeypatch.setattr(plaid_client, "get_transfer", refuse)
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original_all(queue, 0))
    assert main.sweep_withdrawals()["pending"] == 1
    assert ledger.balances("admin") == {"USD": Decimal("40")}


def test_production_plaid_is_for_everyone(as_user, plaid, monkeypatch):
    monkeypatch.setattr(plaid_client, "_keys", {"client_id": "c", "secret": "s", "env": "production"})
    payouts = as_user(ALICE).get("/api/payouts").json()
    assert payouts["provider"] == "plaid" and payouts["test"] is False


def test_stripe_connect_off_is_explained_and_not_offered(as_user, payouts_ready, monkeypatch):
    def refuse(**_):
        raise stripe_client.StripeError(
            "You can only create new accounts if you've signed up for Connect, which you can do at "
            "https://dashboard.stripe.com/connect.", definitive=True)
    monkeypatch.setattr(stripe_client, "create_connected_account", refuse)
    monkeypatch.setattr(main, "_connect_off_until", 0.0)
    client = as_user(ALICE)
    client.patch("/api/me", json={"country": "US"})
    assert client.get("/api/payouts").json()["status"] == "none"
    response = client.post("/api/payouts/onboard")
    assert response.status_code == 503 and response.json()["detail"] == main.CONNECT_OFF
    assert client.get("/api/payouts").json()["status"] == "unavailable"
    # Connect switched on later: asked again once the pause is over
    monkeypatch.setattr(main, "_connect_off_until", 0.0)
    monkeypatch.setattr(stripe_client, "create_connected_account", lambda **_: {"id": "acct_2"})
    assert client.post("/api/payouts/onboard").json()["url"].endswith("acct_2")


def test_a_changed_plaid_secret_reaches_warm_instances(monkeypatch):
    secret = {"value": '{"client_id": "c", "secret": "s", "env": "sandbox"}'}
    class Secrets:
        def get_secret_value(self, SecretId):
            return {"SecretString": secret["value"]}
    monkeypatch.setattr(plaid_client.boto3, "client", lambda service: Secrets())
    monkeypatch.setattr(plaid_client, "_keys", None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(plaid_client.time, "monotonic", lambda: clock["now"])
    assert plaid_client.env() == "sandbox"
    secret["value"] = '{"client_id": "c", "secret": "live", "env": "production"}'
    assert plaid_client.env() == "sandbox"  # read a moment ago
    clock["now"] += plaid_client.KEYS_FOR + 1
    assert plaid_client.env() == "production"


def test_rejected_plaid_keys_are_set_aside_until_the_secret_changes(as_user, payouts_ready, monkeypatch):
    monkeypatch.setattr(plaid_client, "_keys", {"client_id": "c", "secret": "sandbox-secret", "env": "production"})
    monkeypatch.setattr(plaid_client, "_keys_read", 10**9)
    monkeypatch.setattr(plaid_client, "_rejected", None)
    monkeypatch.setattr(plaid_client.time, "monotonic", lambda: 10**9)

    class Rejected:
        status_code = 400
        def json(self):
            return {"error_code": "INVALID_API_KEYS", "error_message": "invalid client_id or secret provided"}
    monkeypatch.setattr(plaid_client.httpx, "post", lambda *a, **k: Rejected())
    client = as_user(ALICE)
    client.patch("/api/me", json={"country": "US"})
    assert client.get("/api/payouts").json()["provider"] == "plaid"
    response = client.post("/api/payouts/onboard", json={})
    assert response.status_code == 502 and response.json()["detail"] == "Bank payouts aren't switched on yet."
    # Set aside: back on Stripe, with no Plaid error
    assert client.get("/api/payouts").json()["provider"] == "stripe"
    # Different keys saved: used again
    monkeypatch.setattr(plaid_client, "_keys", {"client_id": "c", "secret": "production-secret", "env": "production"})
    assert client.get("/api/payouts").json()["provider"] == "plaid"
