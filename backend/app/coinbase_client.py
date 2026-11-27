"""Coinbase (Business or App API, v2 REST): Lyrpay sends Bitcoin rewards from its own Coinbase BTC account.

Keys come from the lyrpay/coinbase secret, JSON {"key_name", "private_key", "account_id"?, "admins_only"?}: a CDP API key
(organizations/<org>/apiKeys/<id>) with the View and Transfer permissions, made in the Coinbase Developer Platform, and its
ES256 (ECDSA) private key in PEM. account_id is Lyrpay's BTC account; without it the BTC account is looked up.

Every request carries its own JWT, signed with the key and good for two minutes (docs.cdp.coinbase.com, "API Key
Authentication"). Sends carry an idem UUID: sending again with the same one returns the first send instead of making
another, so a send whose answer was lost can simply be asked again.

Sends to an email address of a Coinbase user stay inside Coinbase ("off_blockchain"), with no network fee.
"""
import json
import logging
import os
import secrets
import time
from urllib.parse import parse_qsl, urlsplit

import boto3
import httpx
import jwt

SECRET_ID = os.getenv("COINBASE_SECRET_ID", "lyrpay/coinbase")
HOST = "api.coinbase.com"
KEYS_FOR = 300  # the secret is read again every few minutes, so a change needs no redeploy

# Transaction states (Coinbase "Transactions" resource)
SENT = {"completed"}
FAILED = {"canceled", "expired", "failed"}

logger = logging.getLogger("lyrpay")
_http = httpx.Client(timeout=20)
_keys: dict | None = None
_keys_read = 0.0
_account: tuple[str, str] | None = None  # (key name, BTC account id)


class CoinbaseError(RuntimeError):
    """definitive: Coinbase answered and refused (4xx), so nothing was sent. Otherwise the outcome is unknown."""

    def __init__(self, message: str, definitive: bool):
        super().__init__(message)
        self.definitive = definitive


def keys() -> dict | None:
    global _keys, _keys_read
    if _keys is None or time.monotonic() - _keys_read > KEYS_FOR:
        _keys_read = time.monotonic()
        if os.getenv("COINBASE_KEY_NAME"):
            _keys = {"key_name": os.environ["COINBASE_KEY_NAME"], "private_key": os.getenv("COINBASE_PRIVATE_KEY", ""),
                     "account_id": os.getenv("COINBASE_ACCOUNT_ID", "")}
        else:
            try:
                _keys = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"])
            except Exception:
                pass  # keep the keys already read; with none yet, look again next time
    found = _keys or {}
    name, key = str(found.get("key_name") or ""), str(found.get("private_key") or "")
    # A placeholder from an example command counts as no key
    if not name.startswith("organizations/") or "BEGIN" not in key or "PASTE" in (name + key).upper():
        return None
    return found


def ready() -> bool:
    return keys() is not None


def admins_only() -> bool:
    return bool((keys() or {}).get("admins_only"))


def _token(method: str, path: str) -> str:
    found = keys()
    now = int(time.time())
    return jwt.encode(
        {"sub": found["key_name"], "iss": "cdp", "nbf": now, "exp": now + 120, "uri": f"{method} {HOST}{path}"},
        found["private_key"].replace("\\n", "\n"), algorithm="ES256",
        headers={"kid": found["key_name"], "nonce": secrets.token_hex()},
    )


def _call(method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    if not keys():
        raise CoinbaseError("Bitcoin rewards aren't set up yet.", definitive=True)
    try:
        response = _http.request(method, f"https://{HOST}{path}", params=query, json=body,
                                 headers={"Authorization": f"Bearer {_token(method, path)}", "CB-VERSION": "2024-01-01"})
    except httpx.HTTPError as error:
        raise CoinbaseError(f"Coinbase didn't answer: {error}", definitive=False)
    if response.status_code >= 500 or response.status_code == 429:
        raise CoinbaseError(f"Coinbase is busy ({response.status_code}).", definitive=False)
    if response.status_code >= 400:
        try:
            errors = response.json().get("errors") or []
            message = "; ".join(e.get("message", "") for e in errors if isinstance(e, dict)) or response.text
        except ValueError:
            message = response.text
        logger.warning("Coinbase refused %s %s: %s %s", method, path, response.status_code, message[:300])
        raise CoinbaseError(message[:300] or f"Coinbase refused ({response.status_code}).", definitive=True)
    return response.json()


def btc_account() -> str:
    """Lyrpay's BTC account: from the secret, else the first BTC wallet on the key's accounts."""
    global _account
    found = keys() or {}
    if found.get("account_id"):
        return found["account_id"]
    if _account and _account[0] == found.get("key_name"):
        return _account[1]
    path, query = "/v2/accounts", {"limit": 100}
    while path:
        page = _call("GET", path, query=query)
        for account in page.get("data", []):
            if (account.get("currency") or {}).get("code") == "BTC" or account.get("currency") == "BTC":
                _account = (found.get("key_name"), account["id"])
                return account["id"]
        next_uri = urlsplit((page.get("pagination") or {}).get("next_uri") or "")
        path, query = next_uri.path, dict(parse_qsl(next_uri.query))
    raise CoinbaseError("Lyrpay's Coinbase account has no Bitcoin wallet.", definitive=True)


def btc_balance() -> str:
    """What Lyrpay's BTC account holds, as Coinbase shows it."""
    account = _call("GET", f"/v2/accounts/{btc_account()}")["data"]
    return str((account.get("balance") or {}).get("amount") or "0")


def send_btc(to: str, amount: str, idem: str, description: str = "") -> dict:
    """Send BTC from Lyrpay's account. Sending again with the same idem returns that first send, never a second one.
    Returns {"id", "status", "network"}."""
    body = {"type": "send", "to": to, "amount": amount, "currency": "BTC", "idem": idem}
    if description:
        body["description"] = description[:200]
    data = _call("POST", f"/v2/accounts/{btc_account()}/transactions", body)["data"]
    return {"id": data.get("id", ""), "status": data.get("status", "pending"),
            "network": (data.get("network") or {}).get("status", "")}


def transaction(tx_id: str) -> dict:
    data = _call("GET", f"/v2/accounts/{btc_account()}/transactions/{tx_id}")["data"]
    return {"id": data.get("id", tx_id), "status": data.get("status", "pending"),
            "network": (data.get("network") or {}).get("status", "")}
