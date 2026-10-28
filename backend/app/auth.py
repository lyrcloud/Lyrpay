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

_jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", cache_keys=True) if USER_POOL_ID else None


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
    if not _jwks:
        raise HTTPException(status_code=500, detail="Sign-in is not configured.")
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=CLIENT_ID, issuer=ISSUER)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    if claims.get("token_use") != "id":
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return claims


def current_user(authorization: str = Header(default="")) -> User:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Please sign in.")
    return user_from_claims(verify_token(token))
