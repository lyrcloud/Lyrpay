from app import rewards
from tests.test_api import ADMIN, ALICE, CAROL, as_user, aws, top_up  # noqa: F401  (fixtures)


def convert(client, amount, source="USD", target="EUR"):
    response = client.post("/api/convert", json={"source": source, "target": target, "amount": amount})
    assert response.status_code == 200, response.text
    return response.json()


def points(client):
    return client.get("/api/rewards").json()["points"]


def test_conversion_fees_earn_points_and_the_referrer_a_commission(as_user, monkeypatch):
    code = as_user(ALICE).get("/api/rewards").json()["referral"]["code"]
    carol = as_user(CAROL)
    carol.post("/api/referral/claim", json={"code": code})
    top_up(carol, monkeypatch, "carol", "cs_pts_1", amount_total=300000)
    # $2,000 at the 0.5% fee is $10 of fees: 10 points per fee dollar for Carol, 20% of the fee for Alice
    convert(as_user(CAROL), "2000")
    mine = points(as_user(CAROL))
    assert (mine["balance"], mine["lifetime"], mine["tier"]) == (100, 100, "Bronze")
    assert mine["nextTier"] == {"name": "Silver", "at": 500}
    alice = points(as_user(ALICE))
    assert (alice["balance"], alice["affiliatePoints"]) == (200, 200)
    badges = {b["id"]: b["earned"] for b in alice["badges"]}
    assert badges["affiliate"] and not badges["super-affiliate"]
    assert {b["id"] for b in mine["badges"] if b["earned"]} == {"first-topup", "globetrotter"}


def test_points_redeem_into_dollars_once(as_user, monkeypatch):
    carol = as_user(CAROL)
    top_up(carol, monkeypatch, "carol", "cs_pts_2", amount_total=300000)
    convert(as_user(CAROL), "3000")  # $15 of fees: 150 points
    carol = as_user(CAROL)
    assert carol.post("/api/points/redeem", json={"points": 50}).status_code == 400  # below the minimum
    assert carol.post("/api/points/redeem", json={"points": 200}).status_code == 409  # more than held
    redeemed = carol.post("/api/points/redeem", json={"points": 150}).json()
    assert redeemed["usd"] == "1.50" and redeemed["balances"]["USD"] == "1.50"
    assert redeemed["transactions"][0]["counterparty"] == "150 points redeemed"
    assert points(carol)["balance"] == 0 and points(carol)["lifetime"] == 150
    assert carol.post("/api/points/redeem", json={"points": 150}).status_code == 409


def test_admin_sets_the_points_and_commission_rates(as_user, monkeypatch):
    admin = as_user(ADMIN)
    assert admin.put("/api/admin/settings", json={"affiliatePercent": "80"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"pointsPerFeeUsd": "50", "affiliatePercent": "0"}).status_code == 200
    code = as_user(ALICE).get("/api/rewards").json()["referral"]["code"]
    carol = as_user(CAROL)
    carol.post("/api/referral/claim", json={"code": code})
    top_up(carol, monkeypatch, "carol", "cs_pts_3", amount_total=300000)
    convert(as_user(CAROL), "1000")  # $5 of fees
    assert points(as_user(CAROL))["balance"] == 250
    assert points(as_user(ALICE))["balance"] == 0


def test_business_only_promo_code(as_user, monkeypatch):
    admin = as_user(ADMIN)
    made = admin.post("/api/admin/coupons", json={"code": "B2B10", "kind": "percent", "value": "10", "audience": "business"})
    assert made.status_code == 200, made.text
    assert admin.post("/api/admin/coupons", json={"code": "BAD1", "kind": "percent", "value": "10", "audience": "vip"}).status_code == 400
    alice = as_user(ALICE)
    alice.get("/api/me")
    refused = alice.get("/api/coupons/check", params={"code": "B2B10", "amount": "100", "currency": "USD"})
    assert refused.status_code == 400 and "business accounts" in refused.json()["detail"]
    alice.post("/api/business/apply", json={"company": "Alice Imports Ltd", "country": "GB", "registrationId": "GB123456",
                                            "monthlyVolume": "10k-100k"})
    as_user(ADMIN).post("/api/admin/businesses/alice/review", json={"status": "approved"})
    assert as_user(ALICE).get("/api/coupons/check", params={"code": "B2B10", "amount": "100", "currency": "USD"}).json()["bonus"] == "10.00"


def test_tiers_follow_lifetime_points():
    assert [rewards.tier(p) for p in (0, 99, 100, 500, 2000, 5000)] == ["Member", "Member", "Bronze", "Silver", "Gold", "Platinum"]
    assert rewards.next_tier(5000) is None
