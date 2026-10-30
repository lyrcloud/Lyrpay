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
    version: str | None = None,
) -> dict:
    key = secret_key()
    if not key:
        raise StripeError("Payments aren't switched on yet.", definitive=True)
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
    if version:
        headers["Stripe-Version"] = version
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


def create_checkout(*, user_sub: str, email: str, amount: Decimal, currency: str, origin: str, coupon: str = "") -> dict:
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
    if coupon:
        # Read back when the payment is confirmed, to add the promo code's bonus
        data["metadata[coupon]"] = coupon
    return _request("POST", "/checkout/sessions", data)


def get_checkout(session_id: str) -> dict:
    return _request("GET", f"/checkout/sessions/{session_id}")


def get_payment_intent(intent_id: str) -> dict:
    """A bank debit's payment: 'processing' while it's on its way, back to 'requires_payment_method' if the bank refused."""
    return _request("GET", f"/payment_intents/{intent_id}")


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


# Cards: Stripe Issuing makes Visa cards that spend from the Lyrpay balance (cards.py). It must be switched on for the
# Stripe account first (Stripe reviews the business), and cardholders live in the platform's country.

def issuing_enabled() -> bool:
    try:
        _request("GET", "/issuing/cards", params={"limit": 1})
        return True
    except StripeError as error:
        if error.definitive:
            return False
        raise


def create_cardholder(*, user_sub: str, name: str, first_name: str, last_name: str, email: str, phone: str,
                      address: dict, dob: dict, ip: str, user_agent: str, accepted_at: int) -> dict:
    data = {
        "type": "individual", "name": name, "status": "active", "metadata[user]": user_sub,
        "individual[first_name]": first_name, "individual[last_name]": last_name,
        "individual[dob][day]": dob["day"], "individual[dob][month]": dob["month"], "individual[dob][year]": dob["year"],
        "individual[card_issuing][user_terms_acceptance][date]": accepted_at,
        "individual[card_issuing][user_terms_acceptance][ip]": ip,
        "individual[card_issuing][user_terms_acceptance][user_agent]": user_agent[:500],
        **{f"billing[address][{k}]": v for k, v in address.items() if v},
    }
    if email:
        data["email"] = email
    if phone:
        data["phone_number"] = phone
    return _request("POST", "/issuing/cardholders", data, idempotency_key=f"lyrpay-cardholder-{user_sub}")


def create_card(*, user_sub: str, cardholder: str, currency: str, per_authorization_limit: int) -> dict:
    return _request("POST", "/issuing/cards", {
        "cardholder": cardholder, "currency": currency.lower(), "type": "virtual", "status": "active",
        "metadata[user]": user_sub,
        "spending_controls[spending_limits][0][amount]": per_authorization_limit,
        "spending_controls[spending_limits][0][interval]": "per_authorization",
    })


def set_card_status(card_id: str, status: str) -> dict:
    return _request("POST", f"/issuing/cards/{card_id}", {"status": status})


def card_ephemeral_key(card_id: str, nonce: str) -> dict:
    """A short-lived key for Stripe.js to show the card's number and CVC in Stripe's own frames."""
    return _request("POST", "/ephemeral_keys", {"issuing_card": card_id, "nonce": nonce}, version=EPHEMERAL_KEY_VERSION)


EPHEMERAL_KEY_VERSION = "2024-06-20"
