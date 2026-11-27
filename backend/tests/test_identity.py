from app import identity, ledger
from app.auth import user_from_claims
from tests.test_api import ADMIN, ALICE, as_user, aws  # noqa: F401  (fixtures)


def lyrcloud_user(sub, email, verified=True, phone=""):
    claims = {"sub": sub, "email": email, "email_verified": "true" if verified else "false", "name": "Alice A"}
    if phone:
        claims.update(phone_number=phone, phone_number_verified="true")
    user = user_from_claims(claims)
    return identity.wallet_for("lyrcloud", user.sub, user.email, user.phone)


def test_a_lyrcloud_sign_in_joins_the_wallet_with_the_same_verified_email(as_user, monkeypatch):
    monkeypatch.setattr(identity, "_cache", {})
    as_user(ALICE).get("/api/me")  # Alice's Lyr account registers her email for payments
    assert lyrcloud_user("cloud-alice", ALICE.email) == ALICE.sub
    assert ledger.get_item("IDENTITY#lyrcloud#cloud-alice", "IDENTITY")["linkedBy"] == "email"
    # Remembered: the next sign-in reads the link, even after the cache is gone
    monkeypatch.setattr(identity, "_cache", {})
    assert lyrcloud_user("cloud-alice", ALICE.email) == ALICE.sub


def test_unverified_or_new_details_never_reach_someone_elses_wallet(as_user, monkeypatch):
    monkeypatch.setattr(identity, "_cache", {})
    as_user(ALICE).get("/api/me")
    assert lyrcloud_user("cloud-mallory", ALICE.email, verified=False) == "cloud-mallory"  # can't claim Alice's email
    assert lyrcloud_user("cloud-bea", "bea@example.com") == "cloud-bea"  # a new person: a new wallet
    assert ledger.get_item("IDENTITY#lyrcloud#cloud-bea", "IDENTITY")["linkedBy"] == "new"


def test_lyr_accounts_keep_their_wallets(monkeypatch):
    monkeypatch.setattr(identity, "_cache", {})
    assert identity.wallet_for("lyr", "alice", "", "") == "alice"


def test_same_name_and_birthday_is_flagged_never_merged(as_user, monkeypatch):
    for sub, name in (("p1", "José  Núñez"), ("p2", "Nunez Jose"), ("p3", "Someone Else")):
        ledger.table().put_item(Item={"pk": f"USER#{sub}", "sk": "PROFILE", "name": name, "dob": "1990-04-02", "country": "US"})
    groups = as_user(ADMIN).get("/api/admin/duplicates").json()["groups"]
    assert len(groups) == 1 and groups[0]["reason"] == "name+dob"
    assert sorted(a["sub"] for a in groups[0]["accounts"]) == ["p1", "p2"]
    assert as_user(ALICE).get("/api/admin/duplicates").status_code == 403


def test_tokens_from_both_pools_are_checked_against_their_own_keys_and_client(monkeypatch):
    import time

    import jwt
    import pytest
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi import HTTPException

    from app import auth
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Keys:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    cloud = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_cloud"
    monkeypatch.setattr(auth, "POOLS", {cloud: {"name": "lyrcloud", "client": "lyrpay-cloud", "jwks": Keys()}})

    def token(iss=cloud, aud="lyrpay-cloud", use="id"):
        now = int(time.time())
        return jwt.encode({"iss": iss, "aud": aud, "token_use": use, "sub": "s1", "email": "a@example.com",
                           "email_verified": "true", "iat": now, "exp": now + 600}, key, algorithm="RS256")
    assert auth.verify_token(token())["_pool"] == "lyrcloud"
    for bad in (token(iss="https://evil.example.com/pool"), token(aud="someone-else"), token(use="access")):
        with pytest.raises(HTTPException) as refused:
            auth.verify_token(bad)
        assert refused.value.status_code == 401


def test_a_sign_in_with_its_own_wallet_keeps_it(as_user, monkeypatch):
    """Two sign-ins with one email that both already have wallets (e.g. a Google sign-in never merged): neither is
    re-pointed, so no balance is hidden; the admin sees them as possible duplicates instead."""
    monkeypatch.setattr(identity, "_cache", {})
    as_user(ALICE).get("/api/me")
    ledger.table().put_item(Item={"pk": "USER#google-alice", "sk": "PROFILE", "name": "Alice", "email": ALICE.email})
    assert identity.wallet_for("lyr", "google-alice", ALICE.email, "") == "google-alice"


def test_admin_merges_one_persons_two_wallets(as_user, monkeypatch):
    from decimal import Decimal

    from tests.test_api import CAROL, paid_session, stripe_client
    monkeypatch.setattr(identity, "_cache", {})
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("carol"))
    as_user(CAROL).get("/api/me")
    as_user(ALICE).get("/api/me")
    as_user(CAROL).post("/api/topup/confirm", json={"sessionId": "cs_merge_1"})  # Carol: $50
    monkeypatch.setattr(stripe_client, "get_checkout", lambda _: paid_session("alice"))
    as_user(ALICE).post("/api/topup/confirm", json={"sessionId": "cs_merge_2"})  # Alice: $50
    ledger.update_profile("carol", pointsBalance=120)
    identity.wallet_for("lyrcloud", "cloud-carol", "", "")  # a Lyrcloud sign-in that had its own wallet id
    ledger.table().put_item(Item={"pk": "IDENTITY#lyrcloud#cloud-carol", "sk": "IDENTITY", "wallet": "carol", "pool": "lyrcloud", "linkedBy": "email"})

    admin = as_user(ADMIN)
    assert admin.post("/api/admin/merge", json={"source": "carol", "target": "alice", "reason": "x"}).status_code == 400
    response = admin.post("/api/admin/merge", json={"source": "carol", "target": "alice", "reason": "Same person: Google and password accounts"})
    assert response.status_code == 200, response.text
    done = response.json()
    assert done["merged"]["balances"] == {"USD": "50.00"} and done["merged"]["points"] == 120

    assert ledger.balances("alice") == {"USD": Decimal("100")} and ledger.balances("carol") == {}
    assert ledger.get_item("USER#alice", "PROFILE")["pointsBalance"] == 120
    carol = ledger.get_item("USER#carol", "PROFILE")
    assert carol["mergedInto"] == "alice" and carol["frozen"] is True
    # Every way of reaching Carol now reaches Alice's wallet: her own sign-in, her Lyrcloud sign-in, her email
    monkeypatch.setattr(identity, "_cache", {})
    assert identity.wallet_for("lyr", "carol", "", "") == "alice"
    assert identity.wallet_for("lyrcloud", "cloud-carol", "", "") == "alice"
    assert ledger.get_item(f"HANDLE#{CAROL.email}", "HANDLE")["sub"] == "alice"
    assert admin.post("/api/admin/merge", json={"source": "carol", "target": "alice", "reason": "again please"}).status_code == 400
    assert as_user(ALICE).post("/api/admin/merge", json={"source": "a", "target": "b", "reason": "nope nope"}).status_code == 403
