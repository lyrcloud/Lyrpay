"""Pay with Lyrpay: the Lyr apps (Lyrtrip, Lyrshop, Lyrsoft, Lyrcloud) take payments from Lyrpay balances.

An app opens a payment with its secret key; the customer approves it on lyrpay.com/pay/<id>, signed in with the same
Lyr account; the amount moves from their balance to the app's own Lyrpay account in one transaction; Lyrpay tells the
app with a signed callback (and the app can always ask). Refunds go straight back to the customer's balance.

Each app is a merchant: MERCHANT#<id> holds its name, a hash of its secret key (shown once, when made), the secret
its callbacks are signed with, where they go, and the sites customers may be sent back to. Its money is an ordinary
ledger account, USER#merchant_<id>, so balances, statements and withdrawals work as for anyone.

Callbacks: POST to the app's webhook URL, JSON {"type": "payment.paid" | "payment.refunded", "payment": {...}}, header
Lyrpay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>" keyed with the webhook secret>. Anything but
a 2xx is retried by the 15-minute sweep, with the latest state of the payment.
"""
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode, urlsplit

import httpx
from boto3.dynamodb.conditions import Attr

from app import ledger
from app.money import exponent
from app.ledger import TABLE_NAME, AlreadyApplied, InsufficientFunds, _credit, _debit, _now, _put, _transact, _tx_item, table

MERCHANT_ID = re.compile(r"^[a-z][a-z0-9-]{1,23}$")
PAYMENT_ID = re.compile(r"^lpp_[0-9a-f]{24}$")
REFERENCE = re.compile(r"^[A-Za-z0-9_.:#/-]{1,100}$")
DEFAULT_MINUTES, MAX_MINUTES = 30, 1440
CALLBACK_ATTEMPTS = 50  # every 15 minutes: about half a day

logger = logging.getLogger("lyrpay")


class MerchantError(Exception):
    """The app's request is wrong (400) or not allowed (401/409)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def account(merchant_id: str) -> str:
    """The ledger account an app's money is in."""
    return f"merchant_{merchant_id}"


def _fmt(amount, currency: str) -> str:
    """An amount with its currency's minor units (the database drops trailing zeros)."""
    return format(Decimal(amount).quantize(Decimal(1).scaleb(-exponent(currency))), "f")


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ── Merchants (the admin makes them) ──────────────────────────────────────────

def _origins(raw) -> list[str]:
    values = raw if isinstance(raw, list) else str(raw or "").replace(",", " ").split()
    origins = []
    for value in values:
        parts = urlsplit(str(value).strip())
        local = parts.hostname in ("localhost", "127.0.0.1")
        if parts.scheme not in ("https", "http") or not parts.hostname or (parts.scheme == "http" and not local):
            raise MerchantError(f"{value} isn't a site address like https://www.lyrtrip.com.")
        origins.append(f"{parts.scheme}://{parts.netloc}")
    if not origins:
        raise MerchantError("Add at least one site customers come back to, like https://www.lyrtrip.com.")
    return sorted(set(origins))


def save_merchant(body: dict, admin_email: str) -> dict:
    """Make an app a merchant, or change its details. A new merchant (or rotate: true) gets a new secret key and
    webhook secret, returned this once."""
    merchant_id = str(body.get("id") or "").strip().lower()
    if not MERCHANT_ID.match(merchant_id):
        raise MerchantError("The id is 2 to 24 lowercase letters, numbers or dashes, like lyrtrip.")
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:60]
    if not name:
        raise MerchantError("Give the app a name customers will recognise, like Lyrtrip.")
    webhook = str(body.get("webhookUrl") or "").strip()
    if webhook and not re.match(r"^https://[^\s]+$", webhook):
        raise MerchantError("The callback address must start with https://.")
    current = ledger.get_item(f"MERCHANT#{merchant_id}", "MERCHANT")
    item = {
        **(current or {"pk": f"MERCHANT#{merchant_id}", "sk": "MERCHANT", "id": merchant_id, "created": _now()}),
        "name": name, "webhookUrl": webhook, "returnOrigins": _origins(body.get("returnOrigins")),
        "active": body.get("active", True) is not False, "updated": _now(), "updatedBy": admin_email,
    }
    shown = {}
    if not current or body.get("rotate"):
        key = f"lpk_{merchant_id}_{secrets.token_urlsafe(24)}"
        webhook_secret = f"lpwh_{secrets.token_urlsafe(24)}"
        item.update(keyHash=_hash(key), keyHint=key[-4:], webhookSecret=webhook_secret)
        shown = {"secretKey": key, "webhookSecret": webhook_secret}
    table().put_item(Item=item)
    return {**public_merchant(item), **shown}


def public_merchant(item: dict) -> dict:
    balances = {currency: _fmt(amount, currency) for currency, amount in ledger.balances(account(item["id"])).items()}
    return {
        "id": item["id"], "name": item["name"], "webhookUrl": item.get("webhookUrl", ""),
        "returnOrigins": item.get("returnOrigins", []), "active": bool(item.get("active")),
        "keyHint": item.get("keyHint", ""), "created": item.get("created", ""), "balances": balances,
    }


def merchants() -> list[dict]:
    items = ledger._scan(FilterExpression=Attr("sk").eq("MERCHANT"))
    return sorted((public_merchant(i) for i in items), key=lambda m: m["id"])


def authenticate(authorization: str) -> dict:
    """The merchant whose secret key is in 'Authorization: Bearer lpk_<id>_...'."""
    key = authorization.removeprefix("Bearer ").strip()
    match = re.match(r"^lpk_([a-z][a-z0-9-]{1,23})_[A-Za-z0-9_-]{20,}$", key)
    merchant = ledger.get_item(f"MERCHANT#{match.group(1)}", "MERCHANT") if match else None
    if not merchant or not hmac.compare_digest(merchant.get("keyHash", ""), _hash(key)):
        raise MerchantError("That Lyrpay key isn't valid.", 401)
    if not merchant.get("active"):
        raise MerchantError("This app's Lyrpay payments are switched off.", 401)
    return merchant


# ── Payments ──────────────────────────────────────────────────────────────────

def _back_to(merchant: dict, url: str, what: str) -> str:
    url = str(url or "").strip()
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" not in merchant.get("returnOrigins", []):
        raise MerchantError(f"The {what} must be on one of this app's sites: {', '.join(merchant.get('returnOrigins', []))}.")
    return url


def create_payment(merchant: dict, body: dict, parse_amount, currency_code) -> dict:
    """Open a payment, or return the one already opened for this reference (so a retry never charges twice)."""
    reference = str(body.get("reference") or "").strip()
    if not REFERENCE.match(reference):
        raise MerchantError("Send your order number as reference (letters, numbers and _.:#/-, up to 100).")
    existing = ledger.get_item(f"MREF#{merchant['id']}#{reference}", "REF")
    if existing:
        return public_payment(ledger.get_item(f"MPAY#{existing['payment']}", "PAYMENT"))
    currency = currency_code(body.get("currency"))
    amount = parse_amount(str(body.get("amount") or ""), currency)
    try:
        minutes = int(body.get("expiresInMinutes") or DEFAULT_MINUTES)
    except (TypeError, ValueError):
        raise MerchantError("expiresInMinutes must be a whole number.")
    minutes = max(5, min(MAX_MINUTES, minutes))
    payment_id = f"lpp_{secrets.token_hex(12)}"
    item = {
        "pk": f"MPAY#{payment_id}", "sk": "PAYMENT", "id": payment_id, "merchant": merchant["id"],
        "merchantName": merchant["name"], "reference": reference, "amount": amount, "currency": currency,
        "description": re.sub(r"\s+", " ", str(body.get("description") or "")).strip()[:140],
        "returnUrl": _back_to(merchant, body.get("returnUrl"), "return address"),
        "cancelUrl": _back_to(merchant, body.get("cancelUrl") or body.get("returnUrl"), "cancel address"),
        "customerEmail": str(body.get("customerEmail") or "").strip().lower()[:200],
        "status": "pending", "refunded": Decimal(0), "created": _now(),
        "expires": (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(),
    }
    metadata = body.get("metadata")
    if isinstance(metadata, dict):
        item["metadata"] = {str(k)[:40]: str(v)[:200] for k, v in list(metadata.items())[:20]}
    try:
        _transact([
            _put({"pk": f"MREF#{merchant['id']}#{reference}", "sk": "REF", "payment": payment_id}, "attribute_not_exists(pk)"),
            _put(item),
        ], AlreadyApplied())
    except AlreadyApplied:  # opened by a request racing this one
        existing = ledger.get_item(f"MREF#{merchant['id']}#{reference}", "REF")
        return public_payment(ledger.get_item(f"MPAY#{existing['payment']}", "PAYMENT"))
    return public_payment(item)


def public_payment(item: dict) -> dict:
    """A payment as the app sees it."""
    status = item["status"]
    if status == "pending" and item["expires"] < datetime.now(timezone.utc).isoformat():
        status = "expired"
    return {
        "id": item["id"], "object": "payment", "reference": item["reference"], "status": status,
        "amount": _fmt(item["amount"], item["currency"]), "currency": item["currency"],
        "refunded": _fmt(item.get("refunded", 0), item["currency"]),
        "description": item.get("description", ""), "created": item["created"], "expires": item["expires"],
        "paidAt": item.get("paidAt", ""), "customer": item.get("payerEmail", ""), "metadata": item.get("metadata", {}),
        "url": f"{ledger_site()}/pay/{item['id']}",
    }


def ledger_site() -> str:
    from app import mail  # the site address, set per environment
    return mail.SITE_URL


def get_payment(payment_id: str) -> dict:
    item = ledger.get_item(f"MPAY#{payment_id}", "PAYMENT") if PAYMENT_ID.match(payment_id or "") else None
    if not item:
        raise MerchantError("No such payment.", 404)
    return item


def for_merchant(merchant: dict, payment_id: str) -> dict:
    item = get_payment(payment_id)
    if item["merchant"] != merchant["id"]:
        raise MerchantError("No such payment.", 404)
    return item


def checkout_view(item: dict, sub: str) -> dict:
    """What the customer sees on lyrpay.com/pay/<id>."""
    shown = public_payment(item)
    held = ledger.balances(sub).get(item["currency"], Decimal(0))
    return {
        "id": item["id"], "merchant": item["merchantName"], "amount": shown["amount"], "currency": item["currency"],
        "description": shown["description"], "status": shown["status"], "expires": item["expires"],
        "balance": _fmt(held, item["currency"]), "enough": held >= item["amount"], "cancelUrl": item["cancelUrl"],
        "returnUrl": _with_result(item["returnUrl"], item["id"], shown["status"]),
    }


def _with_result(url: str, payment_id: str, status: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode({'lyrpay_payment': payment_id, 'lyrpay_status': status})}"


def approve(item: dict, sub: str, payer_name: str, payer_email: str) -> str:
    """Pay it from the customer's balance. Returns where to send them back to."""
    if item["status"] != "pending":
        if item["status"] == "paid" and item.get("payer") == sub:
            return _with_result(item["returnUrl"], item["id"], "paid")
        raise MerchantError("This payment is no longer open.", 409)
    if item["expires"] < datetime.now(timezone.utc).isoformat():
        raise MerchantError("This payment has expired. Go back to the app and start again.", 409)
    if item.get("customerEmail") and item["customerEmail"] != (payer_email or "").lower():
        raise MerchantError(f"This payment is for {item['customerEmail']}. Sign in with that Lyr account.", 409)
    currency, amount, paid_at = item["currency"], item["amount"], _now()
    try:
        _transact([
            {"Update": {
                "TableName": TABLE_NAME, "Key": {"pk": item["pk"], "sk": "PAYMENT"},
                "UpdateExpression": "SET #s = :paid, payer = :p, payerEmail = :e, payerName = :n, paidAt = :at, queue = :q",
                "ConditionExpression": "#s = :pending",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {":paid": "paid", ":pending": "pending", ":p": sub, ":e": payer_email or "",
                                              ":n": payer_name or "", ":at": paid_at, ":q": "callback"},
            }},
            _debit(sub, currency, amount),
            _credit(account(item["merchant"]), currency, amount),
            _put(_tx_item(sub, "payment", currency, -amount, counterparty=item["merchantName"], note=item.get("description"),
                          reference=item["id"])),
            _put(_tx_item(account(item["merchant"]), "receive", currency, amount, counterparty=payer_name or payer_email,
                          note=item["reference"], reference=item["id"])),
        ], InsufficientFunds())
    except InsufficientFunds:
        # Either the balance is short or the payment was paid a moment ago by another request
        if ledger.get_item(item["pk"], "PAYMENT")["status"] == "paid":
            return _with_result(item["returnUrl"], item["id"], "paid")
        raise MerchantError(f"You don't have enough {currency}. Add money or convert some, then pay.", 409)
    send_callback(ledger.get_item(item["pk"], "PAYMENT"), "payment.paid")
    return _with_result(item["returnUrl"], item["id"], "paid")


def cancel(item: dict) -> str:
    """The customer chose not to pay: the payment closes and they go back to the app."""
    if item["status"] == "pending":
        try:
            table().update_item(Key={"pk": item["pk"], "sk": "PAYMENT"}, UpdateExpression="SET #s = :c",
                                ConditionExpression="#s = :pending", ExpressionAttributeNames={"#s": "status"},
                                ExpressionAttributeValues={":c": "cancelled", ":pending": "pending"})
        except table().meta.client.exceptions.ConditionalCheckFailedException:
            pass
    return _with_result(item["cancelUrl"], item["id"], "cancelled")


def refund(merchant: dict, item: dict, raw_amount, parse_amount) -> dict:
    """Give some or all of a paid payment back to the customer's balance, from the app's Lyrpay account."""
    if item["status"] not in ("paid", "partially_refunded"):
        raise MerchantError("Only a paid payment can be refunded.", 409)
    currency = item["currency"]
    left = item["amount"] - item.get("refunded", Decimal(0))
    amount = parse_amount(str(raw_amount), currency) if raw_amount not in (None, "") else left
    if amount > left:
        raise MerchantError(f"Only {left} {currency} is left to refund.", 409)
    status = "refunded" if amount == left else "partially_refunded"
    try:
        _transact([
            {"Update": {
                "TableName": TABLE_NAME, "Key": {"pk": item["pk"], "sk": "PAYMENT"},
                "UpdateExpression": "SET refunded = refunded + :a, #s = :s, queue = :q",
                "ConditionExpression": "refunded <= :most",
                "ExpressionAttributeNames": {"#s": "status"},
                "ExpressionAttributeValues": {":a": amount, ":s": status, ":most": item["amount"] - amount, ":q": "callback"},
            }},
            _debit(account(merchant["id"]), currency, amount),
            _credit(item["payer"], currency, amount),
            _put(_tx_item(item["payer"], "refund", currency, amount, counterparty=item["merchantName"], reference=item["id"])),
            _put(_tx_item(account(merchant["id"]), "refund", currency, -amount, counterparty=item.get("payerName") or "Customer",
                          note=item["reference"], reference=item["id"])),
        ], InsufficientFunds())
    except InsufficientFunds:
        raise MerchantError(f"Refunded already, or this app's Lyrpay account doesn't hold {amount} {currency}.", 409)
    fresh = ledger.get_item(item["pk"], "PAYMENT")
    send_callback(fresh, "payment.refunded")
    return public_payment(fresh)


# ── Callbacks to the app ──────────────────────────────────────────────────────

def signature(secret: str, timestamp: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def send_callback(item: dict, event: str = "") -> bool:
    """Tell the app about the payment's current state. True once it answered 2xx (the payment leaves the queue)."""
    merchant = ledger.get_item(f"MERCHANT#{item['merchant']}", "MERCHANT") or {}
    url = merchant.get("webhookUrl")
    if not url:
        _callback_done(item, "")
        return True  # nowhere to send it: the app reads payments itself
    if not event:
        event = "payment.refunded" if item["status"] in ("refunded", "partially_refunded") else "payment.paid"
    body = json.dumps({"type": event, "payment": public_payment(item)}, separators=(",", ":")).encode()
    stamp = int(time.time())
    try:
        response = httpx.post(url, content=body, timeout=8, headers={
            "Content-Type": "application/json", "User-Agent": "Lyrpay-Callbacks/1",
            "Lyrpay-Signature": f"t={stamp},v1={signature(merchant['webhookSecret'], stamp, body)}",
        })
        ok = 200 <= response.status_code < 300
        problem = "" if ok else f"HTTP {response.status_code}"
    except httpx.HTTPError as error:
        ok, problem = False, type(error).__name__
    if ok:
        _callback_done(item, event)
    else:
        attempts = int(item.get("callbackAttempts", 0)) + 1
        logger.warning("Lyrpay callback to %s for %s failed (%s), attempt %s", merchant.get("id"), item["id"], problem, attempts)
        update = "SET callbackAttempts = :n, callbackError = :e" + (" REMOVE queue" if attempts >= CALLBACK_ATTEMPTS else "")
        table().update_item(Key={"pk": item["pk"], "sk": "PAYMENT"}, UpdateExpression=update,
                            ExpressionAttributeValues={":n": attempts, ":e": problem})
    return ok


def _callback_done(item: dict, event: str):
    table().update_item(Key={"pk": item["pk"], "sk": "PAYMENT"},
                        UpdateExpression="SET callbackAttempts = :z, lastCallback = :e REMOVE queue, callbackError",
                        ExpressionAttributeValues={":z": 0, ":e": event})


def sweep_callbacks() -> dict:
    """Scheduled: send again the callbacks apps didn't take."""
    outcomes = {"sent": 0, "failed": 0}
    for indexed in ledger.all_pending("callback", 0):
        item = ledger.get_item(indexed["pk"], "PAYMENT")
        if item and item.get("queue") == "callback":
            outcomes["sent" if send_callback(item) else "failed"] += 1
    return outcomes


def recent_payments(merchant_id: str = "", limit: int = 50) -> list[dict]:
    """The latest payments, for the admin."""
    condition = Attr("sk").eq("PAYMENT") & (Attr("merchant").eq(merchant_id) if merchant_id else Attr("merchant").exists())
    items = ledger._scan(FilterExpression=condition)
    items.sort(key=lambda i: i["created"], reverse=True)
    return [{**public_payment(i), "merchant": i["merchant"], "payerName": i.get("payerName", ""),
             "callbackError": i.get("callbackError", "")} for i in items[:limit]]
