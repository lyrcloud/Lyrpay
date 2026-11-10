"""The Lyrpay Stripe App (stripe-app/): panels in the Stripe Dashboard that read Lyrpay's own records.

Each request carries a Stripe-Signature made by Stripe for the Dashboard user, signed with the app's
signing secret (absec_..., shown in the Stripe Dashboard once the app is uploaded) and kept in the
lyrpay/stripe-app secret. Only signatures for Lyrpay's own Stripe account are accepted."""
import json
import os

import boto3

from app import cards

SECRET_ID = os.getenv("STRIPE_APP_SECRET_ID", "lyrpay/stripe-app")

_secret: str | None = None


def app_secret() -> str | None:
    global _secret
    if _secret is None:
        _secret = os.getenv("STRIPE_APP_SECRET") or ""
        if not _secret:
            try:
                _secret = boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
            except Exception:
                _secret = None  # look again next time, so a secret added after the upload is picked up
                return None
    return _secret or None


def verified(header: str, user_id: str, account_id: str, now: float | None = None) -> bool:
    """Stripe signs {"user_id":..., "account_id":...} for the Dashboard user the panel is open for."""
    secret = app_secret()
    if not secret or not user_id or not account_id:
        return False
    payload = json.dumps({"user_id": user_id, "account_id": account_id}, separators=(",", ":")).encode()
    return cards.verify(payload, header, secret, now)
