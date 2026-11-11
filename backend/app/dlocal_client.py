"""Top-ups through dLocal Go (docs.dlocalgo.com): local ways to pay in Latin America, Africa and Asia (Pix, OXXO,
Boleto, PSE, bank transfers, cash vouchers, local cards...). Lyrpay opens a payment, the person pays on dLocal's
checkout page, and dLocal notifies every status change; cash and bank payments can finish days later.

Keys come from the lyrpay/dlocal secret, JSON {"api_key", "secret_key", "env"}, env "sandbox" or "live"
(dashboard.dlocalgo.com > Integrations > API Keys; sandbox keys from dashboard-sbx.dlocalgo.com).
"admins_only": true keeps live keys to admins while they try them."""
import hashlib
import hmac
import json
import logging
import os
import time
from decimal import Decimal

import boto3
import httpx

SECRET_ID = os.getenv("DLOCAL_SECRET_ID", "lyrpay/dlocal")
HOSTS = {"sandbox": "https://api-sbx.dlocalgo.com", "live": "https://api.dlocalgo.com"}
KEYS_FOR = 300

# Where dLocal Go collects payments for a merchant outside these countries, like Lyr Hub LLC in the US
# (helpcenter.dlocalgo.com, "In which countries can I sell with dLocal Go")
COUNTRIES = {
    "AR", "BO", "BR", "CL", "CO", "CR", "EC", "GT", "MX", "PA", "PY", "PE", "DO", "UY",  # Latin America
    "ID", "MY",  # Asia
    "KE", "NG",  # Africa
}

logger = logging.getLogger("lyrpay")

_keys: dict | None = None
_keys_read = 0.0
_currencies: tuple[set, float] | None = None


class DLocalError(RuntimeError):
    """definitive: dLocal answered and refused (4xx). Otherwise the outcome is unknown."""

    def __init__(self, message: str, definitive: bool):
        super().__init__(message)
        self.definitive = definitive


def keys() -> dict | None:
    """api_key, secret_key and env: DLOCAL_* locally, else the lyrpay/dlocal secret. None until one is set."""
    global _keys, _keys_read
    if _keys is None or time.monotonic() - _keys_read > KEYS_FOR:
        _keys_read = time.monotonic()
        if os.getenv("DLOCAL_API_KEY"):
            _keys = {"api_key": os.environ["DLOCAL_API_KEY"], "secret_key": os.getenv("DLOCAL_SECRET_KEY", ""),
                     "env": os.getenv("DLOCAL_ENV", "sandbox")}
        else:
            try:
                _keys = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"])
            except Exception:
                pass  # keep the keys already read; with none yet, look again next time
    if not _keys or not _keys.get("api_key") or not _keys.get("secret_key") or _keys.get("env") not in HOSTS:
        return None
    return _keys


def env() -> str:
    """'live', 'sandbox', or '' while dLocal isn't set up."""
    found = keys()
    return found["env"] if found else ""


def admins_only() -> bool:
    return bool((keys() or {}).get("admins_only"))


def _request(method: str, path: str, body: dict | None = None) -> dict | list:
    found = keys()
    if not found:
        raise DLocalError("Local payments aren't switched on yet.", definitive=True)
    try:
        response = httpx.request(method, f"{HOSTS[found['env']]}{path}", json=body, timeout=20,
                                 headers={"Authorization": f"Bearer {found['api_key']}:{found['secret_key']}"})
        data = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise DLocalError("dLocal didn't answer in time.", definitive=False)
    if response.status_code == 401:
        logger.warning("dLocal refused the keys in %s", SECRET_ID)
    if response.status_code >= 400:
        message = (data.get("message") if isinstance(data, dict) else "") or "dLocal declined the request."
        raise DLocalError(message, definitive=response.status_code < 500)
    return data


def currencies() -> set:
    """Currencies dLocal Go charges in (US dollars plus the local ones it quotes), read every hour."""
    global _currencies
    if _currencies is None or time.monotonic() - _currencies[1] > 3600:
        try:
            rows = _request("GET", "/v1/currency-exchanges")
            _currencies = ({"USD"} | {r["target_currency"].upper() for r in rows if r.get("target_currency")}, time.monotonic())
        except (DLocalError, TypeError, KeyError) as error:
            logger.warning("dLocal wouldn't list its currencies: %s", error)
            return _currencies[0] if _currencies else {"USD"}
    return _currencies[0]


def value(amount: Decimal) -> str:
    return f"{amount:.2f}"


def create_payment(*, order_id: str, amount: Decimal, currency: str, country: str, description: str, email: str,
                   success_url: str, back_url: str, notification_url: str) -> dict:
    """A payment to make on dLocal's checkout page. Returns {id, url}."""
    body = {
        "amount": float(value(amount)),
        "currency": currency.upper(),
        "order_id": order_id,
        "description": description[:100],
        "success_url": success_url,
        "back_url": back_url,
        "notification_url": notification_url,
    }
    if country:
        body["country"] = country.upper()
    if email:
        body["payer"] = {"email": email[:100]}
    payment = _request("POST", "/v1/payments", body)
    if not payment.get("id") or not payment.get("redirect_url"):
        raise DLocalError("dLocal didn't give a page to pay on.", definitive=False)
    return {"id": payment["id"], "url": payment["redirect_url"]}


def get_payment(payment_id: str) -> dict:
    """status: PENDING (status_detail CREATED or PENDING_PAYMENT for cash and bank payments), PAID, REJECTED,
    CANCELLED or EXPIRED."""
    return _request("GET", f"/v1/payments/{payment_id}")


def signature_ok(payload: bytes, authorization: str) -> bool:
    """dLocal signs notifications: Authorization "V2-HMAC-SHA256, Signature: <hex>", an HMAC-SHA256 of the API key
    followed by the body, keyed with the secret key."""
    found = keys()
    if not found or "Signature:" not in authorization:
        return False
    sent = authorization.split("Signature:", 1)[1].strip()
    expected = hmac.new(found["secret_key"].encode(), found["api_key"].encode() + payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sent, expected)
