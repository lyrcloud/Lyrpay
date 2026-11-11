import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ledger, main, merchants
from tests.test_api import ADMIN, ALICE, BOB, as_user, aws, top_up  # noqa: F401  (fixtures)

LYRTRIP = {"id": "lyrtrip", "name": "Lyrtrip", "webhookUrl": "https://api.lyrtrip.com/lyrpay/callback",
           "returnOrigins": "https://www.lyrtrip.com https://lyrtrip.com"}


@pytest.fixture
def callbacks(monkeypatch):
    """Callbacks sent to the apps; answer with state["status"]."""
    state = {"sent": [], "status": 200}

    def post(url, content, timeout, headers):
        state["sent"].append({"url": url, "body": json.loads(content), "raw": content, "headers": headers})
        return httpx.Response(state["status"])

    monkeypatch.setattr(merchants.httpx, "post", post)
    return state


@pytest.fixture
def app_key(as_user, callbacks):
    made = as_user(ADMIN).post("/api/admin/merchants", json=LYRTRIP)
    assert made.status_code == 200, made.text
    return made.json()


def as_app(key):
    return TestClient(main.app, headers={"Authorization": f"Bearer {key}"})


def open_payment(key, **fields):
    body = {"amount": "40", "currency": "USD", "reference": "BK-1001", "description": "Hotel in Lisbon, 2 nights",
            "returnUrl": "https://www.lyrtrip.com/booking/success", "cancelUrl": "https://www.lyrtrip.com/booking/cancel", **fields}
    response = as_app(key).post("/api/v1/payments", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_app_payment_is_paid_from_the_balance_once_and_the_app_is_told(as_user, monkeypatch, app_key, callbacks):
    key = app_key["secretKey"]
    assert key.startswith("lpk_lyrtrip_") and app_key["webhookSecret"].startswith("lpwh_")
    payment = open_payment(key)
    assert payment["status"] == "pending" and payment["url"].endswith(f"/pay/{payment['id']}")
    assert open_payment(key)["id"] == payment["id"]  # the same order number never opens a second payment

    alice = as_user(ALICE)
    top_up(alice, monkeypatch, "alice", "cs_mp_1", amount_total=10000)
    page = as_user(ALICE).get(f"/api/pay/{payment['id']}").json()
    assert (page["merchant"], page["amount"], page["enough"]) == ("Lyrtrip", "40.00", True)
    paid = as_user(ALICE).post(f"/api/pay/{payment['id']}/approve").json()
    assert paid["redirect"] == f"https://www.lyrtrip.com/booking/success?lyrpay_payment={payment['id']}&lyrpay_status=paid"
    assert paid["balances"] == {"USD": "60.00"} and paid["transactions"][0]["counterparty"] == "Lyrtrip"
    again = as_user(ALICE).post(f"/api/pay/{payment['id']}/approve")
    assert again.status_code == 200 and as_user(ALICE).get("/api/me").json()["balances"] == {"USD": "60.00"}

    assert ledger.balances(merchants.account("lyrtrip")) == {"USD": 40}
    assert as_app(key).get(f"/api/v1/payments/{payment['id']}").json()["status"] == "paid"
    sent = callbacks["sent"][0]
    assert sent["url"] == LYRTRIP["webhookUrl"] and sent["body"]["type"] == "payment.paid"
    stamp, signed = (part.split("=", 1)[1] for part in sent["headers"]["Lyrpay-Signature"].split(","))
    assert signed == merchants.signature(app_key["webhookSecret"], int(stamp), sent["raw"])


def test_short_balance_expired_and_wrong_customer_are_refused(as_user, monkeypatch, app_key):
    key = app_key["secretKey"]
    payment = open_payment(key)
    as_user(BOB).get("/api/me")
    short = as_user(BOB).post(f"/api/pay/{payment['id']}/approve")
    assert short.status_code == 409 and "enough USD" in short.json()["detail"]
    mine = open_payment(key, reference="BK-1002", customerEmail="alice@example.com")
    assert "alice@example.com" in as_user(BOB).post(f"/api/pay/{mine['id']}/approve").json()["detail"]
    old = open_payment(key, reference="BK-1003")
    ledger.table().update_item(Key={"pk": f"MPAY#{old['id']}", "sk": "PAYMENT"}, UpdateExpression="SET expires = :e",
                               ExpressionAttributeValues={":e": "2020-01-01T00:00:00+00:00"})
    assert as_app(key).get(f"/api/v1/payments/{old['id']}").json()["status"] == "expired"
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_mp_2", amount_total=10000)
    assert as_user(ALICE).post(f"/api/pay/{old['id']}/approve").status_code == 409


def test_cancel_sends_the_customer_back_and_closes_it(as_user, app_key):
    payment = open_payment(app_key["secretKey"])
    as_user(ALICE).get("/api/me")
    back = as_user(ALICE).post(f"/api/pay/{payment['id']}/cancel").json()["redirect"]
    assert back.startswith("https://www.lyrtrip.com/booking/cancel?") and "lyrpay_status=cancelled" in back
    assert as_user(ALICE).post(f"/api/pay/{payment['id']}/approve").status_code == 409


def test_refunds_go_back_to_the_balance_never_more_than_paid(as_user, monkeypatch, app_key, callbacks):
    key = app_key["secretKey"]
    payment = open_payment(key)
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_mp_3", amount_total=10000)
    as_user(ALICE).post(f"/api/pay/{payment['id']}/approve")
    part = as_app(key).post(f"/api/v1/payments/{payment['id']}/refund", json={"amount": "15"}).json()
    assert (part["status"], part["refunded"]) == ("partially_refunded", "15.00")
    assert as_app(key).post(f"/api/v1/payments/{payment['id']}/refund", json={"amount": "30"}).status_code == 409
    rest = as_app(key).post(f"/api/v1/payments/{payment['id']}/refund").json()
    assert (rest["status"], rest["refunded"]) == ("refunded", "40.00")
    assert as_user(ALICE).get("/api/me").json()["balances"] == {"USD": "100.00"}
    assert ledger.balances(merchants.account("lyrtrip")) == {}
    assert [c["body"]["type"] for c in callbacks["sent"]] == ["payment.paid", "payment.refunded", "payment.refunded"]


def test_failed_callback_is_retried_by_the_sweep(as_user, monkeypatch, app_key, callbacks):
    payment = open_payment(app_key["secretKey"])
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_mp_4", amount_total=10000)
    callbacks["status"] = 500
    as_user(ALICE).post(f"/api/pay/{payment['id']}/approve")
    assert merchants.sweep_callbacks() == {"sent": 0, "failed": 1}
    callbacks["status"] = 200
    assert merchants.sweep_callbacks() == {"sent": 1, "failed": 0}
    assert merchants.sweep_callbacks() == {"sent": 0, "failed": 0}


def test_keys_and_sites_are_checked(as_user, app_key):
    assert as_app("lpk_lyrtrip_" + "x" * 32).post("/api/v1/payments", json={}).status_code == 401
    assert TestClient(main.app).get("/api/v1/payments/lpp_" + "0" * 24).status_code == 401
    elsewhere = as_app(app_key["secretKey"]).post("/api/v1/payments", json={
        "amount": "5", "currency": "USD", "reference": "X1", "returnUrl": "https://evil.example/steal"})
    assert elsewhere.status_code == 400 and "lyrtrip.com" in elsewhere.json()["detail"]
    other = as_user(ADMIN).post("/api/admin/merchants", json={**LYRTRIP, "id": "lyrshop", "name": "Lyrshop",
                                                               "returnOrigins": "https://lyrshop.com"}).json()
    lyrtrip_payment = open_payment(app_key["secretKey"])
    assert as_app(other["secretKey"]).get(f"/api/v1/payments/{lyrtrip_payment['id']}").status_code == 404
    rotated = as_user(ADMIN).post("/api/admin/merchants", json={**LYRTRIP, "rotate": True}).json()
    assert as_app(app_key["secretKey"]).get(f"/api/v1/payments/{lyrtrip_payment['id']}").status_code == 401
    assert as_app(rotated["secretKey"]).get(f"/api/v1/payments/{lyrtrip_payment['id']}").status_code == 200
    assert as_user(ALICE).post("/api/admin/merchants", json=LYRTRIP).status_code == 403
