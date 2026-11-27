from app import kyc, ledger
from tests.test_api import ADMIN, ALICE, as_user, aws, payouts_ready  # noqa: F401  (fixtures)


def fake_twilio(monkeypatch, line="mobile", approve=True):
    sent = []

    def call(method, url, data=None):
        sent.append((method, url, data))
        if "lookups" in url:
            return {"_status": 200, "valid": True, "line_type_intelligence": {"type": line, "carrier_name": "Jio"}}
        if url.endswith("/v2/Services"):
            return {"_status": 201, "sid": "VA123"}
        if url.endswith("VerificationCheck"):
            return {"_status": 200, "status": "approved" if approve else "pending"}
        return {"_status": 201, "status": "pending"}
    monkeypatch.setattr(kyc, "_twilio_call", call)
    return sent


def test_mobile_number_is_verified_by_code(as_user, monkeypatch):
    sent = fake_twilio(monkeypatch)
    alice = as_user(ALICE)
    assert alice.get("/api/kyc").json()["kyc"]["status"] == "unverified"
    assert alice.post("/api/kyc/phone", json={"phone": "+91 98765 43210"}).json()["sent"] is True
    assert alice.post("/api/kyc/phone/check", json={"code": "12"}).status_code == 400
    result = alice.post("/api/kyc/phone/check", json={"code": "123456"}).json()["kyc"]
    assert result["level"] == 1 and result["status"] == "phone" and result["phone"]["number"] == "+919876543210"
    # The number now finds Alice for payments, and the Verify service was made once
    assert ledger.find_recipient("+919876543210")["sub"] == "alice"
    assert sum(1 for _, url, _ in sent if url.endswith("/v2/Services")) == 1


def test_virtual_numbers_are_refused(as_user, monkeypatch):
    fake_twilio(monkeypatch, line="nonFixedVoip")
    response = as_user(ALICE).post("/api/kyc/phone", json={"phone": "+14155550100"})
    assert response.status_code == 400 and "mobile number your bank" in response.json()["detail"]


def test_withdrawals_wait_for_the_level_the_admin_asks(as_user, monkeypatch, payouts_ready):
    fake_twilio(monkeypatch)
    as_user(ALICE).get("/api/me")
    as_user(ADMIN).put("/api/admin/settings", json={"kycWithdrawLevel": "1"})
    response = as_user(ALICE).post("/api/withdraw", json={"amount": "1"})
    assert response.status_code == 403 and "Verify your mobile number" in response.json()["detail"]
    # The admin can mark someone verified by hand
    acc = as_user(ADMIN).post("/api/admin/users/alice/kyc", json={"override": "verified", "note": "seen in person"}).json()["account"]
    assert acc["kyc"]["level"] == 2
    assert as_user(ALICE).post("/api/withdraw", json={"amount": "1"}).status_code != 403
