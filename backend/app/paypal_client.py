"""PayPal top-ups through the PayPal REST API (Orders v2): the person approves the payment on PayPal, comes back,
and Lyrpay captures it. Keys come from the lyrpay/paypal secret, JSON {"client_id", "secret", "env"}, where env is
"sandbox" or "live" (a PayPal Business account's REST app, developer.paypal.com > Apps & Credentials)."""
import json
import logging
import os
import time
from decimal import Decimal

import boto3
import httpx

SECRET_ID = os.getenv("PAYPAL_SECRET_ID", "lyrpay/paypal")
HOSTS = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}

# Warm instances live for hours, so the secret is read again every few minutes: a change reaches them without a redeploy
KEYS_FOR = 300

# Currencies PayPal takes for payments between accounts in different countries (BRL, MYR and CNY are in-country
# only, so they go the US dollar way like any other). HUF, JPY and TWD take whole amounts only.
CURRENCIES = {
    "AUD", "CAD", "CHF", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "ILS", "JPY", "MXN", "NOK", "NZD", "PHP",
    "PLN", "SEK", "SGD", "THB", "TWD", "USD",
}
WHOLE = {"HUF", "JPY", "TWD"}

logger = logging.getLogger("lyrpay")

_keys: dict | None = None
_keys_read = 0.0
_token: tuple[str, float, tuple] | None = None  # access token, when it expires, the keys it belongs to


class PayPalError(RuntimeError):
    """definitive: PayPal answered and refused (4xx). Otherwise the outcome is unknown and the call may have worked."""

    def __init__(self, message: str, definitive: bool, issue: str = ""):
        super().__init__(message)
        self.definitive = definitive
        self.issue = issue


def keys() -> dict | None:
    """client_id, secret and env: PAYPAL_* locally, else the lyrpay/paypal secret. None until one is set."""
    global _keys, _keys_read
    if _keys is None or time.monotonic() - _keys_read > KEYS_FOR:
        _keys_read = time.monotonic()
        if os.getenv("PAYPAL_CLIENT_ID"):
            _keys = {"client_id": os.environ["PAYPAL_CLIENT_ID"], "secret": os.getenv("PAYPAL_SECRET", ""),
                     "env": os.getenv("PAYPAL_ENV", "sandbox")}
        else:
            try:
                _keys = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"])
            except Exception:
                pass  # keep the keys already read; with none yet, look again next time
    if not _keys or not _keys.get("client_id") or not _keys.get("secret") or _keys.get("env") not in HOSTS:
        return None
    return _keys


def env() -> str:
    """'live', 'sandbox', or '' while PayPal isn't set up."""
    found = keys()
    return found["env"] if found else ""


def _access_token(found: dict) -> str:
    global _token
    fingerprint = (found["client_id"], found["secret"], found["env"])
    if _token and _token[2] == fingerprint and time.time() < _token[1]:
        return _token[0]
    try:
        response = httpx.post(f"{HOSTS[found['env']]}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                              auth=(found["client_id"], found["secret"]), timeout=20)
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise PayPalError("PayPal didn't answer in time.", definitive=False)
    if response.status_code >= 400:
        logger.warning("PayPal refused the keys in %s: %s", SECRET_ID, body.get("error_description") or body)
        raise PayPalError("PayPal payments aren't switched on yet.", definitive=True)
    # Renewed a minute early, so a token never runs out mid-request
    _token = (body["access_token"], time.time() + int(body.get("expires_in", 300)) - 60, fingerprint)
    return _token[0]


def _request(method: str, path: str, body: dict | None = None, request_id: str | None = None) -> dict:
    found = keys()
    if not found:
        raise PayPalError("PayPal payments aren't switched on yet.", definitive=True)
    headers = {"Authorization": f"Bearer {_access_token(found)}", "Prefer": "return=representation"}
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # PayPal's idempotency key: a repeat returns the first result
    try:
        response = httpx.request(method, f"{HOSTS[found['env']]}{path}", json=body, headers=headers, timeout=20)
        data = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise PayPalError("PayPal didn't answer in time.", definitive=False)
    if response.status_code == 401:
        global _token
        _token = None
    if response.status_code >= 400:
        details = data.get("details") or [{}]
        issue = details[0].get("issue", "") or data.get("name", "")
        message = details[0].get("description") or data.get("message") or "PayPal declined the request."
        raise PayPalError(message, definitive=response.status_code < 500, issue=issue)
    return data


def payable(currency: str, amount: Decimal) -> bool:
    """Whether PayPal can charge this amount in this currency (otherwise it's paid in US dollars)."""
    currency = currency.upper()
    return currency in CURRENCIES and (currency not in WHOLE or amount == amount.to_integral_value())


def value(amount: Decimal, currency: str) -> str:
    return f"{amount:.{0 if currency.upper() in WHOLE else 2}f}"


def create_order(*, reference: str, amount: Decimal, currency: str, description: str, origin: str) -> dict:
    """An order for the person to approve on PayPal. Returns {id, url}: url is PayPal's page to approve it on.
    PayPal sends them back to origin with ?topup=paypal&token=<order id> (or ?topup=cancelled)."""
    order = _request("POST", "/v2/checkout/orders", {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference[:256],
            "custom_id": reference[:127],
            "description": description[:127],
            "amount": {"currency_code": currency.upper(), "value": value(amount, currency)},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Lyrpay",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": f"{origin}/?topup=paypal",
            "cancel_url": f"{origin}/?topup=cancelled",
        }}},
    }, request_id=f"lyrpay-order-{reference}")
    url = next((link["href"] for link in order.get("links", []) if link.get("rel") in ("payer-action", "approve")), "")
    if not url:
        raise PayPalError("PayPal didn't give a page to pay on.", definitive=False)
    return {"id": order["id"], "url": url}


def get_order(order_id: str) -> dict:
    return _request("GET", f"/v2/checkout/orders/{order_id}")


def capture(order_id: str) -> dict:
    """Take the money for an order the person approved. Safe to repeat: the same capture comes back."""
    return _request("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id=f"lyrpay-capture-{order_id}")


def get_capture(capture_id: str) -> dict:
    """A capture PayPal held as PENDING (an eCheck, a review): later COMPLETED, or DECLINED / FAILED."""
    return _request("GET", f"/v2/payments/captures/{capture_id}")


def first_capture(order: dict) -> dict | None:
    units = order.get("purchase_units") or [{}]
    captures = ((units[0].get("payments") or {}).get("captures")) or []
    return captures[0] if captures else None
