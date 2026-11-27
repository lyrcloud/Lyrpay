from urllib.parse import quote

from app import ledger
from tests.test_api import ADMIN, ALICE, BOB, as_user, aws, top_up  # noqa: F401  (fixtures)


def account(client, sub="alice"):
    return client.get(f"/api/admin/users/{sub}").json()["account"]


def test_only_admins_reach_accounts(as_user):
    as_user(ALICE).get("/api/me")
    assert as_user(ALICE).get("/api/admin/users/alice").status_code == 403
    assert as_user(ADMIN).get("/api/admin/users/nobody").status_code == 404


def test_admin_adjusts_and_sets_balances_with_a_visible_line(as_user, monkeypatch):
    as_user(ALICE).get("/api/me")
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_adm_1")  # 50 USD
    admin = as_user(ADMIN)
    admin.get("/api/me")
    result = admin.post("/api/admin/users/alice/balance", json={"currency": "USD", "amount": "-60", "note": "test"})
    assert result.status_code == 409  # below zero without allowNegative
    admin.post("/api/admin/users/alice/balance", json={"currency": "USD", "amount": "+5.25", "note": "Goodwill"})
    acc = admin.post("/api/admin/users/alice/balance", json={"currency": "EUR", "amount": "12", "mode": "set"}).json()["account"]
    assert acc["balances"]["USD"] == "55.25" and acc["balances"]["EUR"] == "12"
    me = as_user(ALICE).get("/api/me").json()
    assert me["transactions"][0]["kind"] == "adjust" and me["transactions"][1]["note"] == "Goodwill"
    assert [e["action"] for e in as_user(ADMIN).get("/api/admin/audit").json()["entries"]][:2] == ["balance.adjust", "balance.adjust"]


def test_admin_edits_and_deletes_lines_with_or_without_the_balance(as_user, monkeypatch):
    as_user(ALICE).get("/api/me")
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_adm_2")
    admin = as_user(ADMIN)
    admin.get("/api/me")
    line = account(admin)["transactions"][0]
    sk = quote(line["sk"], safe="")
    # Change the amount and move the balance by the difference
    acc = admin.patch(f"/api/admin/users/alice/transactions/{sk}", json={"fields": {"amount": "40", "note": "fixed"}, "rebalance": True}).json()["account"]
    assert acc["balances"]["USD"] == "40.00" and acc["transactions"][0]["note"] == "fixed"
    # Only the line changes
    acc = admin.patch(f"/api/admin/users/alice/transactions/{sk}", json={"fields": {"amount": "45"}}).json()["account"]
    assert acc["balances"]["USD"] == "40.00"
    # A hand-written line, then deleting the first one and undoing its effect
    admin.post("/api/admin/users/alice/transactions", json={"fields": {"kind": "bonus", "currency": "USD", "amount": "3"}, "rebalance": True})
    acc = admin.post(f"/api/admin/users/alice/transactions/{sk}/delete", json={"rebalance": True}).json()["account"]
    assert acc["balances"]["USD"] == "-2.00" and [t["kind"] for t in acc["transactions"]] == ["bonus"]


def test_frozen_account_can_look_but_not_move_money(as_user, monkeypatch):
    as_user(ALICE).get("/api/me")
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_adm_3")
    as_user(BOB).get("/api/me")
    as_user(ADMIN).patch("/api/admin/users/alice", json={"fields": {"frozen": True, "name": "Alice Admin-set"}})
    alice = as_user(ALICE)
    assert alice.get("/api/me").status_code == 200
    assert alice.post("/api/transfer", json={"to": BOB.phone, "amount": "1", "currency": "USD"}).status_code == 423
    # The admin's name sticks through the next sign-in
    assert alice.get("/api/me").json()["profile"]["name"] == "Alice Admin-set"
    as_user(ADMIN).patch("/api/admin/users/alice", json={"fields": {"frozen": False}})
    assert as_user(ALICE).post("/api/transfer", json={"to": BOB.phone, "amount": "1", "currency": "USD"}).status_code == 200


def test_admin_deletes_an_account(as_user, monkeypatch):
    as_user(ALICE).get("/api/me")
    top_up(as_user(ALICE), monkeypatch, "alice", "cs_adm_4")
    admin = as_user(ADMIN)
    assert admin.post("/api/admin/users/alice/delete", json={"confirm": "nope"}).status_code == 400
    assert admin.post("/api/admin/users/alice/delete", json={"confirm": "DELETE", "reason": "asked"}).json()["deleted"] >= 3
    assert ledger.find_recipient(ALICE.email) is None
    assert admin.get("/api/admin/users/alice").status_code == 404
