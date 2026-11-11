"""Withdrawals through Wise: Lyrpay's Wise Business account pays people's bank accounts in their own currency.

Keys come from the lyrpay/wise secret, JSON {"token", "env", "source", "private_key"}: a personal API token (Wise >
Settings > API tokens, with 2-step verification on), env "sandbox" or "live", source the Wise balance currency payouts
are paid from (default USD), and private_key (PEM, optional) when Wise asks for payouts to be signed (strong customer
authentication: the matching public key is added next to the token in Wise)."""
import base64
import json
import logging
import os
import time
import uuid
from decimal import Decimal

import boto3
import httpx

SECRET_ID = os.getenv("WISE_SECRET_ID", "lyrpay/wise")
HOSTS = {"sandbox": "https://api.wise-sandbox.com", "live": "https://api.wise.com"}
KEYS_FOR = 300  # the secret is read again every few minutes, so a change needs no redeploy

# Transfer states (docs.wise.com, "Transfer statuses"): sent, or never going to arrive (the money goes back on the balance)
SENT = {"outgoing_payment_sent"}
FAILED = {"cancelled", "funds_refunded", "bounced_back", "charged_back"}

logger = logging.getLogger("lyrpay")

_keys: dict | None = None
_keys_read = 0.0
_profile: tuple[tuple, int] | None = None  # (keys fingerprint, business profile id)


class WiseError(RuntimeError):
    """definitive: Wise answered and refused (4xx). Otherwise the outcome is unknown and the call may have worked."""

    def __init__(self, message: str, definitive: bool, code: str = ""):
        super().__init__(message)
        self.definitive = definitive
        self.code = code


def keys() -> dict | None:
    global _keys, _keys_read
    if _keys is None or time.monotonic() - _keys_read > KEYS_FOR:
        _keys_read = time.monotonic()
        if os.getenv("WISE_TOKEN"):
            _keys = {"token": os.environ["WISE_TOKEN"], "env": os.getenv("WISE_ENV", "sandbox"),
                     "source": os.getenv("WISE_SOURCE", "USD"), "private_key": os.getenv("WISE_PRIVATE_KEY", "")}
        else:
            try:
                _keys = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"])
            except Exception:
                pass  # keep the keys already read; with none yet, look again next time
    if not _keys or not _keys.get("token") or _keys.get("env") not in HOSTS:
        return None
    return _keys


def env() -> str:
    """'live', 'sandbox', or '' while Wise isn't set up."""
    found = keys()
    return found["env"] if found else ""


def admins_only() -> bool:
    return bool((keys() or {}).get("admins_only"))


def source() -> str:
    found = keys() or {}
    return (found.get("source") or "USD").upper()


def _sign(found: dict, one_time_token: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(found["private_key"].encode(), password=None)
    return base64.b64encode(key.sign(one_time_token.encode(), padding.PKCS1v15(), hashes.SHA256())).decode()


def _request(method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict | list:
    found = keys()
    if not found:
        raise WiseError("Wise withdrawals aren't switched on yet.", definitive=True)
    headers = {"Authorization": f"Bearer {found['token']}", "Accept-Minor-Version": "1"}
    url = f"{HOSTS[found['env']]}{path}"
    try:
        response = httpx.request(method, url, json=body, params=params, headers=headers, timeout=25)
        # Strong customer authentication: Wise asks for its one-time token back, signed with our private key
        if response.status_code == 403 and response.headers.get("x-2fa-approval"):
            token = response.headers["x-2fa-approval"]
            if not found.get("private_key"):
                logger.warning("Wise wants %s %s signed: add private_key to %s and its public key in Wise", method, path, SECRET_ID)
                raise WiseError("Wise needs this payout approved. Lyrpay is on it.", definitive=False, code="sca")
            headers.update({"x-2fa-approval": token, "X-Signature": _sign(found, token)})
            response = httpx.request(method, url, json=body, params=params, headers=headers, timeout=25)
        data = response.json() if response.content else {}
    except (httpx.HTTPError, ValueError):
        raise WiseError("Wise didn't answer in time.", definitive=False)
    if response.status_code == 401:
        logger.warning("Wise refused the token in %s", SECRET_ID)
        raise WiseError("Wise withdrawals aren't switched on yet.", definitive=True, code="unauthorized")
    if response.status_code >= 400:
        raise WiseError(_message(data), definitive=response.status_code < 500, code=_code(data))
    return data


def _errors(data) -> list:
    if isinstance(data, dict):
        return data.get("errors") or ([data] if data.get("message") or data.get("error") else [])
    return []


def _message(data) -> str:
    errors = _errors(data)
    if errors:
        first = errors[0]
        return first.get("message") or first.get("error_description") or first.get("error") or "Wise refused the request."
    return "Wise refused the request."


def _code(data) -> str:
    errors = _errors(data)
    return (errors[0].get("code") or errors[0].get("error") or "") if errors else ""


def profile_id() -> int:
    """Lyrpay's Wise business profile (the token's personal profile if it has no business one)."""
    global _profile
    found = keys()
    fingerprint = (found or {}).get("token"), (found or {}).get("env")
    if _profile and _profile[0] == fingerprint:
        return _profile[1]
    profiles = _request("GET", "/v2/profiles")
    if not profiles:
        raise WiseError("The Wise account has no profile.", definitive=True)
    chosen = next((p for p in profiles if p.get("type", "").upper() == "BUSINESS"), profiles[0])
    _profile = (fingerprint, chosen["id"])
    return chosen["id"]


def requirements(currency: str, amount: Decimal, details: dict | None = None) -> list:
    """The bank details Wise needs for a payout in `currency`: one entry per kind of account (IBAN, sort code, ...),
    each with its form fields. details: what's filled in so far, for fields that change the rest of the form."""
    params = {"source": source(), "target": currency.upper(), "sourceAmount": str(max(amount, Decimal(100)))}
    if details:
        return _request("POST", "/v1/account-requirements", details, params=params)
    return _request("GET", "/v1/account-requirements", params=params)


def nest(flat: dict) -> dict:
    """Wise form keys are dotted paths ("address.city"); its API wants them nested."""
    nested: dict = {}
    for key, value in flat.items():
        if value in ("", None):
            continue
        node = nested
        *parents, last = key.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value
    return nested


def create_recipient(*, currency: str, kind: str, holder: str, details: dict) -> dict:
    return _request("POST", "/v1/accounts", {
        "profile": profile_id(), "currency": currency.upper(), "type": kind, "accountHolderName": holder,
        "ownedByCustomer": False, "details": details,
    })


def quote(*, target: str, source_amount: Decimal, recipient: int | None = None) -> dict:
    """What a payout costs: Wise's fee comes out of source_amount (in the source currency) and the rest arrives.
    Returns {id, fee, receive, rate} for a payout from the Wise balance."""
    body = {"sourceCurrency": source(), "targetCurrency": target.upper(), "sourceAmount": float(source_amount)}
    if recipient:
        body["targetAccount"] = recipient
    found = _request("POST", f"/v3/profiles/{profile_id()}/quotes", body)
    options = [o for o in found.get("paymentOptions", []) if o.get("payIn") == "BALANCE" and not o.get("disabled")]
    option = next((o for o in options if o.get("payOut") == found.get("payOut")), options[0] if options else None)
    if not option:
        raise WiseError(f"Wise can't send {target.upper()} from the Lyrpay balance right now.", definitive=True)
    return {"id": found["id"], "fee": Decimal(str(option["fee"]["total"])), "receive": Decimal(str(option["targetAmount"])),
            "rate": Decimal(str(found.get("rate", 1))), "warning": (found.get("notices") or [{}])[0].get("text", "")}


def transaction_id(reference: str) -> str:
    """Wise de-duplicates transfers by customerTransactionId (a UUID): the same withdrawal always gets the same one."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"lyrpay-withdrawal:{reference}"))


def create_transfer(*, recipient: int, quote_id: str, reference: str) -> dict:
    return _request("POST", "/v1/transfers", {
        "targetAccount": recipient, "quoteUuid": quote_id, "customerTransactionId": transaction_id(reference),
        "details": {"reference": "Lyrpay"},
    })


def fund(transfer_id: int) -> dict:
    """Pay a transfer from the Wise balance. Until then it waits as incoming_payment_waiting."""
    return _request("POST", f"/v3/profiles/{profile_id()}/transfers/{transfer_id}/payments", {"type": "BALANCE"})


def get_transfer(transfer_id: int) -> dict:
    return _request("GET", f"/v1/transfers/{transfer_id}")


def cancel(transfer_id: int) -> dict:
    return _request("PUT", f"/v1/transfers/{transfer_id}/cancel")
