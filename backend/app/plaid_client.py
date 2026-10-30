import json
import os
from decimal import Decimal

import boto3
import httpx

SECRET_ID = os.getenv("PLAID_SECRET_ID", "lyrpay/plaid")
HOSTS = {"sandbox": "https://sandbox.plaid.com", "production": "https://production.plaid.com"}

_keys: dict | None = None


class PlaidError(RuntimeError):
    """definitive: Plaid answered and refused (4xx). Otherwise the outcome is unknown and the call may have worked."""

    def __init__(self, message: str, definitive: bool):
        super().__init__(message)
        self.definitive = definitive


def keys() -> dict | None:
    """client_id, secret and env ('sandbox' or 'production'): PLAID_* locally, else the lyrpay/plaid secret (JSON).
    None until one is set."""
    global _keys
    if _keys is None:
        if os.getenv("PLAID_CLIENT_ID"):
            _keys = {"client_id": os.environ["PLAID_CLIENT_ID"], "secret": os.getenv("PLAID_SECRET", ""),
                     "env": os.getenv("PLAID_ENV", "sandbox")}
        else:
            try:
                _keys = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=SECRET_ID)["SecretString"])
            except Exception:
                return None  # look again next time, so a secret added later is picked up
    return _keys if _keys and _keys.get("env") in HOSTS else None


def env() -> str | None:
    return (keys() or {}).get("env")


def _request(path: str, body: dict) -> dict:
    found = keys()
    if not found:
        raise PlaidError("Bank payouts aren't switched on yet.", definitive=True)
    try:
        response = httpx.post(
            f"{HOSTS[found['env']]}{path}", json={"client_id": found["client_id"], "secret": found["secret"], **body},
            timeout=20,
        )
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise PlaidError("Plaid didn't answer in time.", definitive=False)
    if response.status_code == 400 and data.get("error_code") == "INVALID_API_KEYS":
        global _keys
        _keys = None  # rejected keys: read the secret again next time, so corrected ones are picked up
    if response.status_code >= 400:
        message = data.get("display_message") or data.get("error_message") or "Plaid declined the request."
        raise PlaidError(message, definitive=response.status_code < 500)
    return data


def hosted_link(user_sub: str, redirect: str | None) -> dict:
    """A Plaid Hosted Link page where the person picks their bank and account. Returns link_token and hosted_link_url;
    without a redirect (the mobile app) Plaid ends on its own done screen."""
    return _request("/link/token/create", {
        "client_name": "Lyrpay", "language": "en", "country_codes": ["US"], "products": ["transfer"],
        "user": {"client_user_id": user_sub},
        "hosted_link": {"completion_redirect_uri": redirect} if redirect else {},
    })


def linked_account(link_token: str) -> dict | None:
    """The bank account chosen on a Hosted Link page, once the person finished: public_token, account_id and a label."""
    for session in _request("/link/token/get", {"link_token": link_token}).get("link_sessions") or []:
        for result in (session.get("results") or {}).get("item_add_results") or []:
            accounts = [a for a in result.get("accounts") or [] if a.get("type") == "depository"]
            if accounts:
                account = accounts[0]
                bank = (result.get("institution") or {}).get("name") or account.get("name") or "Bank"
                return {"public_token": result["public_token"], "account_id": account["id"],
                        "label": f"{bank} ••{account.get('mask') or ''}".strip()}
    return None


def exchange(public_token: str) -> str:
    return _request("/item/public_token/exchange", {"public_token": public_token})["access_token"]


def pay_out(access_token: str, account_id: str, amount: Decimal, legal_name: str, reference: str) -> dict:
    """Send `amount` USD to the person's bank by ACH. Safe to repeat: the authorization is keyed on the Lyrpay
    withdrawal and Plaid makes one transfer per authorization, so a retry returns the transfer already made.
    Raises PlaidError (definitive) when Plaid declines it."""
    authorization = _request("/transfer/authorization/create", {
        "access_token": access_token, "account_id": account_id, "type": "credit", "network": "ach",
        "ach_class": "ppd", "amount": f"{amount:.2f}", "user": {"legal_name": legal_name or "Lyrpay customer"},
        "idempotency_key": reference,
    })["authorization"]
    if authorization["decision"] != "approved":
        rationale = authorization.get("decision_rationale") or {}
        raise PlaidError(rationale.get("description") or "Plaid declined the withdrawal.", definitive=True)
    return _request("/transfer/create", {
        "access_token": access_token, "account_id": account_id, "authorization_id": authorization["id"],
        "description": "Lyrpay", "metadata": {"lyrpay_tx": reference},
    })["transfer"]


def get_transfer(transfer_id: str) -> dict:
    return _request("/transfer/get", {"transfer_id": transfer_id})["transfer"]
