from decimal import Decimal

import pytest

from app import ledger, main, paypal_client
from tests.test_api import ALICE, BOB, as_user, aws  # noqa: F401  (fixtures)


@pytest.fixture
def paypal(monkeypatch):
    """A pretend PayPal: orders made, approved by the payer, then captured once."""
    state = {"orders": {}, "captures": 0, "capture_status": "COMPLETED"}
    monkeypatch.setattr(paypal_client, "env", lambda: "live")

    def create_order(*, reference, amount, currency, description, origin):
        order_id = f"ORDER{len(state['orders']) + 1:05d}XYZ"
        state["orders"][order_id] = {"id": order_id, "status": "CREATED", "currency": currency,
                                     "value": paypal_client.value(amount, currency)}
        return {"id": order_id, "url": f"https://www.paypal.com/checkoutnow?token={order_id}"}

    def order_view(order_id):
        order = state["orders"][order_id]
        view = {"id": order_id, "status": order["status"]}
        if order["status"] == "COMPLETED":
            view["purchase_units"] = [{"payments": {"captures": [{
                "id": f"CAP{order_id}", "status": state["capture_status"],
                "amount": {"currency_code": order["currency"], "value": order["value"]},
            }]}}]
        return view

    def capture(order_id):
        state["captures"] += 1
        state["orders"][order_id]["status"] = "COMPLETED"
        return order_view(order_id)

    def get_capture(capture_id):
        order = state["orders"][capture_id.removeprefix("CAP")]
        return {"id": capture_id, "status": state["capture_status"], "amount": {"currency_code": order["currency"], "value": order["value"]}}

    monkeypatch.setattr(paypal_client, "create_order", create_order)
    monkeypatch.setattr(paypal_client, "get_order", order_view)
    monkeypatch.setattr(paypal_client, "capture", capture)
    monkeypatch.setattr(paypal_client, "get_capture", get_capture)
    state["approve"] = lambda ref: state["orders"][ref.removeprefix("paypal_")].update(status="APPROVED")
    return state


def start(client, amount="20", currency="EUR"):
    response = client.post("/api/topup", json={"amount": amount, "currency": currency, "method": "paypal"})
    assert response.status_code == 200, response.text
    return response.json()


def test_paypal_topup_is_captured_and_credited_once(as_user, paypal):
    client = as_user(ALICE)
    started = start(client)
    assert started["url"].startswith("https://www.paypal.com/") and started["sessionId"].startswith("paypal_")
    # Not approved on PayPal yet: nothing taken, nothing credited
    assert client.post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).status_code == 409
    paypal["approve"](started["sessionId"])
    first = client.post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).json()
    again = client.post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).json()
    assert first["credited"] and not again["credited"]
    assert again["balances"] == {"EUR": "20.00"} and paypal["captures"] == 1
    assert again["transactions"][0]["counterparty"] == "PayPal"


def test_paypal_order_for_someone_else_is_refused(as_user, paypal):
    started = start(as_user(ALICE))
    paypal["approve"](started["sessionId"])
    assert as_user(BOB).post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).status_code == 409
    assert paypal["captures"] == 0


def test_unknown_paypal_reference_is_refused(as_user, paypal):
    assert as_user(ALICE).post("/api/topup/confirm", json={"sessionId": "paypal_NOTOURS12345"}).status_code == 400


def test_currency_paypal_cannot_charge_is_paid_in_dollars(as_user, paypal, monkeypatch):
    client = as_user(ALICE)
    started = start(client, "1000", "JPY")  # JPY is fine
    assert paypal["orders"][started["sessionId"].removeprefix("paypal_")]["currency"] == "JPY"
    monkeypatch.setattr(main.rates, "usd_rates", lambda: {"updated": "today", "rates": {"USD": 1, "EUR": 0.9, "JPY": 150, "NGN": 1500}})
    started = start(client, "1500", "NGN")
    order = paypal["orders"][started["sessionId"].removeprefix("paypal_")]
    assert (order["currency"], order["value"]) == ("USD", "1.00")
    paypal["approve"](started["sessionId"])
    assert client.post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).json()["balances"]["NGN"] == "1500.00"


def test_held_paypal_payment_is_credited_by_the_sweep(as_user, paypal, monkeypatch):
    client = as_user(ALICE)
    started = start(client)
    paypal["approve"](started["sessionId"])
    paypal["capture_status"] = "PENDING"
    held = client.post("/api/topup/confirm", json={"sessionId": started["sessionId"]}).json()
    assert held["pending"] and held["balances"] == {}
    paypal["capture_status"] = "COMPLETED"
    original = ledger.all_pending
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original(queue, 0))
    assert main.sweep_topups()["paid"] == 1
    assert main.statement("alice")["balances"] == {"EUR": "20.00"}


def test_sandbox_paypal_is_for_admins_only(as_user, paypal, monkeypatch):
    monkeypatch.setattr(paypal_client, "env", lambda: "sandbox")
    response = as_user(ALICE).post("/api/topup", json={"amount": "20", "currency": "EUR", "method": "paypal"})
    assert response.status_code == 400
    assert not as_user(ALICE).get("/api/topup/methods?currency=EUR").json()["paypal"]


def test_whole_amount_currencies():
    assert paypal_client.payable("JPY", Decimal("100")) and not paypal_client.payable("JPY", Decimal("100.5"))
    assert paypal_client.value(Decimal("100"), "JPY") == "100" and paypal_client.value(Decimal("5"), "EUR") == "5.00"
