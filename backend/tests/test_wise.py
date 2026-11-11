from decimal import Decimal

import pytest

from app import ledger, main, wise_client
from tests.test_api import ALICE, BOB, as_user, aws  # noqa: F401  (fixtures)


@pytest.fixture
def wise(monkeypatch):
    """A pretend Wise: recipients, quotes with a 1 USD fee, transfers paid from the balance."""
    state = {"transfers": {}, "funded": 0, "fund_error": None, "after_fund": "processing", "recipients": []}
    monkeypatch.setattr(wise_client, "env", lambda: "live")
    monkeypatch.setattr(wise_client, "source", lambda: "USD")
    monkeypatch.setattr(main.rates, "usd_rates", lambda: {"updated": "today", "rates": {"USD": 1, "EUR": 0.9, "GBP": 0.8}})

    def create_recipient(*, currency, kind, holder, details):
        state["recipients"].append({"currency": currency, "type": kind, "holder": holder, "details": details})
        return {"id": 1000 + len(state["recipients"])}

    def quote(*, target, source_amount, recipient=None):
        rate = {"USD": Decimal(1), "EUR": Decimal("0.9"), "GBP": Decimal("0.8")}[target]
        return {"id": "q-1", "fee": Decimal(1), "receive": ((source_amount - 1) * rate).quantize(Decimal("0.01")),
                "rate": rate, "warning": ""}

    def create_transfer(*, recipient, quote_id, reference):
        # Same withdrawal, same transfer
        for tid, t in state["transfers"].items():
            if t["reference"] == reference:
                return {"id": tid, "status": t["status"]}
        tid = 5000 + len(state["transfers"])
        state["transfers"][tid] = {"reference": reference, "status": "incoming_payment_waiting", "recipient": recipient}
        return {"id": tid, "status": "incoming_payment_waiting"}

    def fund(tid):
        if state["fund_error"]:
            raise state["fund_error"]
        state["funded"] += 1
        state["transfers"][tid]["status"] = state["after_fund"]
        return {"status": "COMPLETED"}

    def cancel(tid):
        state["transfers"][tid]["status"] = "cancelled"
        return {}

    monkeypatch.setattr(wise_client, "create_recipient", create_recipient)
    monkeypatch.setattr(wise_client, "quote", quote)
    monkeypatch.setattr(wise_client, "create_transfer", create_transfer)
    monkeypatch.setattr(wise_client, "fund", fund)
    monkeypatch.setattr(wise_client, "cancel", cancel)
    monkeypatch.setattr(wise_client, "get_transfer", lambda tid: {"id": tid, **state["transfers"][tid]})
    return state


def fund_wallet(sub, currency, amount):
    ledger.credit_topup(sub, f"cs_{sub}_{currency}", currency, Decimal(amount))


def add_iban(client):
    response = client.post("/api/wise/recipient", json={"currency": "EUR", "type": "iban", "details": {
        "accountHolderName": "Alice Example", "iban": "DE89370400440532013000", "address.city": "Berlin", "address.country": "DE",
    }})
    assert response.status_code == 200, response.text
    return response.json()


def test_wise_withdrawal_in_euros(as_user, wise, monkeypatch):
    client = as_user(ALICE)
    fund_wallet("alice", "EUR", "100")
    assert client.post("/api/withdraw", json={"amount": "50", "currency": "EUR", "rail": "wise"}).status_code == 409
    saved = add_iban(client)
    assert saved["wise"]["accounts"]["EUR"] == "Alice Example · iban ••3000"
    assert wise["recipients"][0]["details"] == {"iban": "DE89370400440532013000", "address": {"city": "Berlin", "country": "DE"},
                                                "legalType": "PRIVATE"}
    quote = client.get("/api/wise/quote?currency=EUR&amount=45").json()
    assert (quote["receive"], quote["fee"]) == ("44.10", "0.90")  # 50 USD less the 1 USD fee, at 0.9
    result = client.post("/api/withdraw", json={"amount": "45", "currency": "EUR", "rail": "wise"}).json()
    assert result["status"] == "pending" and result["balances"] == {"EUR": "55.00"}
    assert wise["funded"] == 1
    # Wise sends it: the next look settles the line as paid
    (tid,) = wise["transfers"]
    wise["transfers"][tid]["status"] = "outgoing_payment_sent"
    original = ledger.pending
    monkeypatch.setattr(ledger, "pending", lambda sub, kind, older_than_seconds=60: original(sub, kind, 0))
    me = client.get("/api/me").json()
    line = next(t for t in me["transactions"] if t["kind"] == "withdraw")
    assert line["status"] == "paid" and line["counterparty"].startswith("Alice Example")


def test_refused_wise_transfer_restores_balance(as_user, wise, monkeypatch):
    client = as_user(ALICE)
    fund_wallet("alice", "EUR", "100")
    add_iban(client)

    def refuse(**_):
        raise wise_client.WiseError("Recipient account is invalid", definitive=True)
    monkeypatch.setattr(wise_client, "create_transfer", refuse)
    response = client.post("/api/withdraw", json={"amount": "40", "currency": "EUR", "rail": "wise"})
    assert response.status_code == 502
    assert main.statement("alice")["balances"] == {"EUR": "100.00"}


def test_unfunded_wise_transfer_is_cancelled_and_refunded(as_user, wise):
    client = as_user(ALICE)
    fund_wallet("alice", "EUR", "100")
    add_iban(client)
    wise["fund_error"] = wise_client.WiseError("Insufficient funds", definitive=True)
    response = client.post("/api/withdraw", json={"amount": "40", "currency": "EUR", "rail": "wise"})
    assert response.status_code == 502
    assert main.statement("alice")["balances"] == {"EUR": "100.00"}
    assert [t["status"] for t in wise["transfers"].values()] == ["cancelled"]


def test_unanswered_wise_withdrawal_is_finished_by_the_sweep(as_user, wise, monkeypatch):
    client = as_user(ALICE)
    fund_wallet("alice", "EUR", "100")
    add_iban(client)
    wise["fund_error"] = wise_client.WiseError("Wise didn't answer in time.", definitive=False)
    result = client.post("/api/withdraw", json={"amount": "40", "currency": "EUR", "rail": "wise"}).json()
    assert result["status"] == "pending" and result["balances"] == {"EUR": "60.00"}
    wise["fund_error"], wise["after_fund"] = None, "outgoing_payment_sent"
    original = ledger.all_pending
    monkeypatch.setattr(ledger, "all_pending", lambda queue, older_than_seconds=60: original(queue, 0))
    main.sweep_withdrawals()  # pays the waiting transfer
    assert main.sweep_withdrawals()["paid"] == 1
    assert len(wise["transfers"]) == 1 and wise["funded"] == 1


def test_wise_sandbox_is_for_admins_only(as_user, wise, monkeypatch):
    monkeypatch.setattr(wise_client, "env", lambda: "sandbox")
    assert as_user(BOB).post("/api/wise/requirements", json={"currency": "EUR"}).status_code == 503


def test_nest_and_label():
    assert wise_client.nest({"a.b": "1", "a.c": "2", "d": "", "e": "3"}) == {"a": {"b": "1", "c": "2"}, "e": "3"}
    assert wise_client.transaction_id("tx1") == wise_client.transaction_id("tx1") != wise_client.transaction_id("tx2")


def test_live_wise_can_be_admins_only(as_user, wise, monkeypatch):
    monkeypatch.setattr(wise_client, "admins_only", lambda: True)
    assert as_user(BOB).post("/api/wise/requirements", json={"currency": "EUR"}).status_code == 503
    monkeypatch.setattr(wise_client, "admins_only", lambda: False)
    assert main.wise_payouts("someone@example.com")
