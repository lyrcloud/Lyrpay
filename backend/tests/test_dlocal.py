import hashlib
import hmac
import json
import pytest

from app import dlocal_client, ledger, main
from tests.test_api import ALICE, BOB, as_user, aws  # noqa: F401  (fixtures)
from tests.test_api import ADMIN

KEYS = {"api_key": "ak_test", "secret_key": "sk_test", "env": "live"}


@pytest.fixture
def dlocal(monkeypatch):
    """A pretend dLocal Go: payments opened, then paid, left pending (cash/bank) or rejected on its checkout page."""
    state = {"payments": {}, "keys": dict(KEYS)}
    monkeypatch.setattr(main.rates, "usd_rates",
                        lambda: {"updated": "today", "rates": {"USD": 1, "EUR": 0.9, "BRL": 5, "MXN": 18}})
    monkeypatch.setattr(dlocal_client, "keys", lambda: state["keys"])
    monkeypatch.setattr(dlocal_client, "currencies", lambda: {"USD", "BRL", "MXN"})

    def create_payment(*, order_id, amount, currency, country, description, email, success_url, back_url, notification_url):
        payment_id = f"DP-{len(state['payments']) + 1}"
        state["payments"][payment_id] = {"id": payment_id, "status": "PENDING", "status_detail": "CREATED",
                                         "amount": float(amount), "currency": currency, "order_id": order_id}
        state["last"] = {"country": country, "success_url": success_url, "notification_url": notification_url}
        return {"id": payment_id, "url": f"https://checkout.dlocalgo.com/validate/{payment_id}"}

    monkeypatch.setattr(dlocal_client, "create_payment", create_payment)
    monkeypatch.setattr(dlocal_client, "get_payment", lambda payment_id: dict(state["payments"][payment_id]))
    state["set"] = lambda payment_id, **fields: state["payments"][payment_id].update(fields)
    return state


def start(client, amount="100", currency="BRL"):
    response = client.post("/api/topup", json={"amount": amount, "currency": currency, "method": "dlocal"})
    assert response.status_code == 200, response.text
    return response.json()["sessionId"]


def payment_of(ref):
    return ledger.get_item(f"DLOCAL#{ref}", "ORDER")["payment"]


def confirm(client, ref):
    return client.post("/api/topup/confirm", json={"sessionId": ref})


def notify(client, payment_id, keys=KEYS):
    body = json.dumps({"payment_id": payment_id}).encode()
    signature = hmac.new(keys["secret_key"].encode(), keys["api_key"].encode() + body, hashlib.sha256).hexdigest()
    return client.post("/api/dlocal/notify", content=body, headers={"Authorization": f"V2-HMAC-SHA256, Signature: {signature}"})


def test_paid_dlocal_topup_is_credited_once(as_user, dlocal):
    client = as_user(ALICE)
    ref = start(client)
    assert ref.startswith("dlocal_") and dlocal["last"]["success_url"].endswith(f"/?topup={ref}")
    assert dlocal["last"]["notification_url"].endswith("/api/dlocal/notify")
    assert confirm(client, ref).status_code == 409  # nothing paid yet
    dlocal["set"](payment_of(ref), status="PAID", status_detail=None)
    first, again = confirm(client, ref).json(), confirm(client, ref).json()
    assert first["credited"] and not again["credited"]
    assert again["balances"] == {"BRL": "100.00"} and again["transactions"][0]["counterparty"] == "dLocal"


def test_cash_payment_waits_then_the_notification_credits_it(as_user, dlocal):
    client = as_user(ALICE)
    ref = start(client, "250", "MXN")
    payment_id = payment_of(ref)
    dlocal["set"](payment_id, status_detail="PENDING_PAYMENT")  # an OXXO voucher, not paid at the shop yet
    waiting = confirm(client, ref).json()
    assert waiting["pending"] and waiting["balances"] == {}
    dlocal["set"](payment_id, status="PAID", status_detail=None)
    assert notify(client, payment_id).status_code == 200
    assert notify(client, payment_id).status_code == 200  # dLocal may send it twice
    assert main.statement(ALICE.sub)["balances"] == {"MXN": "250.00"}


def test_expired_voucher_fails_the_pending_line(as_user, dlocal, monkeypatch):
    client = as_user(ALICE)
    ref = start(client)
    payment_id = payment_of(ref)
    dlocal["set"](payment_id, status_detail="PENDING_PAYMENT")
    confirm(client, ref)
    dlocal["set"](payment_id, status="EXPIRED")
    original = ledger.all_pending
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original(queue, 0))
    assert main.sweep_topups()["failed"] == 1
    statement = main.statement(ALICE.sub)
    assert statement["balances"] == {} and statement["transactions"][0]["status"] == "failed"


def test_notification_with_a_bad_signature_is_refused(as_user, dlocal):
    client = as_user(ALICE)
    ref = start(client)
    payment_id = payment_of(ref)
    dlocal["set"](payment_id, status="PAID", status_detail=None)
    assert notify(client, payment_id, {**KEYS, "secret_key": "guessed"}).status_code == 401
    assert main.statement(ALICE.sub)["balances"] == {}


def test_amount_dlocal_reports_must_match(as_user, dlocal):
    client = as_user(ALICE)
    ref = start(client)
    dlocal["set"](payment_of(ref), status="PAID", status_detail=None, amount=1.0)
    assert confirm(client, ref).status_code == 409
    assert main.statement(ALICE.sub)["balances"] == {}


def test_someone_elses_dlocal_payment_is_refused(as_user, dlocal):
    ref = start(as_user(ALICE))
    dlocal["set"](payment_of(ref), status="PAID", status_detail=None)
    assert confirm(as_user(BOB), ref).status_code == 409


def test_currency_dlocal_cannot_charge_is_paid_in_dollars(as_user, dlocal, monkeypatch):
    monkeypatch.setattr(main.rates, "usd_rates", lambda: {"updated": "today", "rates": {"USD": 1, "EUR": 0.8}})
    client = as_user(ALICE)
    ref = start(client, "20", "EUR")
    payment = dlocal["payments"][payment_of(ref)]
    assert (payment["currency"], payment["amount"]) == ("USD", 25.0)
    dlocal["set"](payment["id"], status="PAID", status_detail=None)
    assert confirm(client, ref).json()["balances"] == {"EUR": "20.00"}


def test_dlocal_is_offered_where_it_collects(as_user, dlocal):
    client = as_user(ALICE)
    client.patch("/api/me", json={"country": "BR"})
    assert client.get("/api/topup/methods").json()["dlocal"]
    client.patch("/api/me", json={"country": "FR"})
    assert not client.get("/api/topup/methods").json()["dlocal"]


def test_sandbox_or_admins_only_dlocal_is_for_admins(as_user, dlocal):
    for keys in ({**KEYS, "env": "sandbox"}, {**KEYS, "admins_only": True}):
        dlocal["keys"] = keys
        alice = as_user(ALICE)
        alice.patch("/api/me", json={"country": "BR"})
        assert not alice.get("/api/topup/methods").json()["dlocal"]
        assert alice.post("/api/topup", json={"amount": "10", "currency": "BRL", "method": "dlocal"}).status_code == 400
        admin = as_user(ADMIN).get("/api/topup/methods").json()
        assert admin["dlocal"] and admin["dlocalTest"] == (keys["env"] == "sandbox")
