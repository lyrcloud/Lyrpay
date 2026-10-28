import os
from decimal import Decimal

import boto3
import httpx

from app.money import THREE_DECIMAL, to_minor

API = "https://api.stripe.com/v1"
SECRET_ID = os.getenv("STRIPE_SECRET_ID", "lyrpay/stripe")

_key: str | None = None


def secret_key() -> str | None:
    """The Stripe secret key: STRIPE_SECRET_KEY locally, else the lyrpay/stripe secret. None until one is set."""
    global _key
    if _key is None:
        _key = os.getenv("STRIPE_SECRET_KEY") or ""
        if not _key:
            try:
                _key = boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
            except Exception:
                return None
    return _key or None


def _request(method: str, path: str, data: dict | None = None) -> dict:
    key = secret_key()
    if not key:
        raise RuntimeError("Card payments aren't switched on yet.")
    response = httpx.request(method, f"{API}{path}", data=data, auth=(key, ""), timeout=20)
    body = response.json()
    if response.status_code >= 400:
        raise RuntimeError(body.get("error", {}).get("message", "Stripe declined the request."))
    return body


def stripe_amount(amount: Decimal, currency: str) -> int:
    minor = to_minor(amount, currency)
    # Stripe takes three-decimal currencies in minor units but only to the nearest 10
    return minor - minor % 10 if currency.upper() in THREE_DECIMAL else minor


def create_checkout(*, user_sub: str, email: str, amount: Decimal, currency: str, origin: str) -> dict:
    # No payment_method_types: Checkout offers every method switched on in the
    # Stripe dashboard that fits the currency and the payer's country
    # (cards, wallets, bank debits and local methods).
    data = {
        "mode": "payment",
        "client_reference_id": user_sub,
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": currency.lower(),
        "line_items[0][price_data][unit_amount]": stripe_amount(amount, currency),
        "line_items[0][price_data][product_data][name]": "Lyrpay balance top-up",
        "metadata[user]": user_sub,
        "metadata[purpose]": "lyrpay_topup",
        "success_url": f"{origin}/?topup={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{origin}/?topup=cancelled",
    }
    if email:
        data["customer_email"] = email
    return _request("POST", "/checkout/sessions", data)


def get_checkout(session_id: str) -> dict:
    return _request("GET", f"/checkout/sessions/{session_id}")
