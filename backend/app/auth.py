import json
import os
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException

# Lyrpay shares the Lyr account pool with Lyrtrip, so one sign-in (email,
# mobile number or Google) works across the apps. The browser sends its
# Cognito ID token, which is checked against the pool's keys and Lyrpay's
# own app client.
REGION = os.getenv("COGNITO_REGION", "us-east-1")
USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# Mobile-number accounts carry a stand-in email made from the number; it is never mailed.
PHONE_LOGIN_DOMAIN = "@phone.lyrtrip.com"

# Lyrcloud accounts (Lyrcloud, Lyrsoft) sign in too, through Lyrpay's own app client in the Lyrcloud pool; identity.py
# maps each sign-in to one wallet, so a person with both accounts has one Lyrpay
LYRCLOUD_POOL_ID = os.getenv("LYRCLOUD_USER_POOL_ID", "us-east-1_cWNxc9tns")
LYRCLOUD_CLIENT_ID = os.getenv("LYRCLOUD_CLIENT_ID", "400r1qrclm20r9ni9s69de6tr6")


def _pool(pool_id: str, client_id: str, name: str) -> tuple[str, dict]:
    issuer = f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}"
    return issuer, {"name": name, "client": client_id, "jwks": jwt.PyJWKClient(f"{issuer}/.well-known/jwks.json", cache_keys=True)}


POOLS = dict(
    ([_pool(USER_POOL_ID, CLIENT_ID, "lyr")] if USER_POOL_ID else [])
    + ([_pool(LYRCLOUD_POOL_ID, LYRCLOUD_CLIENT_ID, "lyrcloud")] if LYRCLOUD_POOL_ID and LYRCLOUD_CLIENT_ID else [])
)
_jwks = POOLS[ISSUER]["jwks"] if ISSUER in POOLS else None


@dataclass
class User:
    sub: str
    email: str
    phone: str
    name: str


def _verified(claims: dict, key: str) -> bool:
    return str(claims.get(f"{key}_verified", "")).lower() == "true"


def user_from_claims(claims: dict) -> User:
    email = str(claims.get("email") or "").lower()
    # Google accounts come through Cognito without email_verified; Google has already checked it
    federated = bool(claims.get("identities"))
    if email.endswith(PHONE_LOGIN_DOMAIN) or not (federated or _verified(claims, "email")):
        email = ""
    phone = str(claims.get("phone_number") or "") if _verified(claims, "phone_number") else ""
    name = str(claims.get("name") or "")
    if not name and isinstance(claims.get("identities"), str):
        try:
            name = json.loads(claims["identities"])[0].get("userId", "")
        except (ValueError, IndexError, AttributeError):
            pass
    return User(sub=str(claims["sub"]), email=email, phone=phone, name=name or email or phone)


def verify_token(token: str) -> dict:
    """The ID token's claims, checked against the pool that issued it and Lyrpay's app client there.
    Adds "_pool": which Lyr sign-in it came from."""
    if not POOLS:
        raise HTTPException(status_code=500, detail="Sign-in is not configured.")
    try:
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss", "")
        pool = POOLS.get(issuer)
        if not pool:
            raise HTTPException(status_code=401, detail="Please sign in again.")
        key = pool["jwks"].get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=pool["client"], issuer=issuer)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return {**claims, "_pool": pool["name"]}


def current_user(authorization: str = Header(default="")) -> User:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Please sign in.")
    claims = verify_token(token)
    user = user_from_claims(claims)
    # Whichever sign-in it is, it reaches the person's one wallet (identity.py)
    from app import identity
    user.sub = identity.wallet_for(claims.get("_pool", "lyr"), user.sub, user.email, user.phone)
    return user
