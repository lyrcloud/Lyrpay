import os
from decimal import Decimal

import boto3
import httpx

from app.money import THREE_DECIMAL, to_minor

API = "https://api.stripe.com/v1"
SECRET_ID = os.getenv("STRIPE_SECRET_ID", "lyrpay/stripe")

_key: str | None = None


class StripeError(RuntimeError):
    """definitive: Stripe answered and refused (4xx). Otherwise the outcome is unknown and the call may have worked."""

    def __init__(self, message: str, definitive: bool):
        super().__init__(message)
        self.definitive = definitive


def secret_key() -> str | None:
    """The Stripe secret key: STRIPE_SECRET_KEY locally, else the lyrpay/stripe secret. None until one is set."""
    global _key
    if _key is None:
        _key = os.getenv("STRIPE_SECRET_KEY") or ""
        if not _key:
            try:
                _key = boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"].strip()
            except Exception:
                _key = None  # look again next time, so a secret added later is picked up
                return None
    return _key or None


def _request(
    method: str, path: str, data: dict | None = None, idempotency_key: str | None = None, params: dict | None = None,
) -> dict:
    key = secret_key()
    if not key:
        raise StripeError("Payments aren't switched on yet.", definitive=True)
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    try:
        response = httpx.request(method, f"{API}{path}", params=params, data=data, auth=(key, ""), headers=headers, timeout=20)
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise StripeError("Stripe didn't answer in time.", definitive=False)
    if response.status_code == 401:
        global _key
        _key = None  # rejected key: read the secret again next time, so a corrected one is picked up
    if response.status_code >= 400:
        message = body.get("error", {}).get("message", "Stripe declined the request.")
        raise StripeError(message, definitive=response.status_code < 500)
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


# Withdrawals: each person gets a Stripe Connect Express account, where Stripe collects their identity and
# bank details. A withdrawal moves money from the platform balance to that account, and Stripe pays it out
# to their bank on its usual schedule.

_platform: dict | None = None


def platform() -> dict:
    """The platform's own country and settlement currency; withdrawals are made in that currency."""
    global _platform
    if _platform is None:
        account = _request("GET", "/account")
        _platform = {"country": account["country"], "currency": account["default_currency"].upper()}
    return _platform


def create_connected_account(*, user_sub: str, email: str, country: str) -> dict:
    data = {
        "type": "express",
        "country": country,
        "capabilities[transfers][requested]": "true",
        "metadata[user]": user_sub,
    }
    if email:
        data["email"] = email
    # Outside the platform's country, accounts can only receive transfers (Stripe's recipient agreement)
    if country != platform()["country"]:
        data["tos_acceptance[service_agreement]"] = "recipient"
    return _request("POST", "/accounts", data, idempotency_key=f"lyrpay-account-{user_sub}-{country}")


def get_account(account_id: str) -> dict:
    return _request("GET", f"/accounts/{account_id}")


def onboarding_link(account_id: str, origin: str) -> str:
    return _request("POST", "/account_links", {
        "account": account_id,
        "type": "account_onboarding",
        "refresh_url": f"{origin}/?payouts=refresh",
        "return_url": f"{origin}/?payouts=done",
    })["url"]


def transfer_to(account_id: str, amount: Decimal, currency: str, reference: str) -> dict:
    return _request("POST", "/transfers", {
        "amount": stripe_amount(amount, currency),
        "currency": currency.lower(),
        "destination": account_id,
        "metadata[lyrpay_tx]": reference,
    }, idempotency_key=f"lyrpay-withdraw-{reference}")


def find_transfer(account_id: str, reference: str) -> dict | None:
    """The transfer made for a Lyrpay withdrawal, if Stripe has one. Checked before any retry, since
    idempotency keys only last 24 hours."""
    transfers = _request("GET", "/transfers", params={"destination": account_id, "limit": 100})["data"]
    return next((t for t in transfers if t.get("metadata", {}).get("lyrpay_tx") == reference), None)
