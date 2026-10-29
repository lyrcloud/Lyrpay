"""Lyrpay Visa cards through Stripe Issuing, spending straight from the Lyrpay balance.

When a card is used, Stripe asks us (issuing_authorization.request) and we approve only if the balance in the card's
currency covers it, holding that amount at once. Each authorization keeps a record of how much of the balance it
holds now ("covered") and how much the merchant has taken ("captured"), so events can arrive in any order:
  - a capture raises captured, and debits more if it went over what's covered (tips, fuel, late charges);
  - when the authorization closes (captured, reversed or expired), whatever is covered beyond captured goes back;
  - a refund credits the balance.
Each Stripe transaction is applied once. Card numbers never touch Lyrpay: Stripe.js shows them in Stripe's frames.
"""
import hashlib
import hmac
import logging
import os
import re
import time
from datetime import date
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app import ledger, stripe_client
from app.ledger import TABLE_NAME, _now, _put, _tx_item, table
from app.money import from_minor

WEBHOOK_SECRET_ID = os.getenv("STRIPE_ISSUING_WEBHOOK_SECRET_ID", "lyrpay/stripe-issuing-webhook")
PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
CARD_TERMS_URL = os.getenv("CARD_TERMS_URL", "")
# Stripe also stops any single payment above this (in the card currency's minor units); the balance check comes first
PER_AUTHORIZATION_LIMIT = int(os.getenv("CARD_PER_AUTHORIZATION_LIMIT", "250000"))
SIGNATURE_TOLERANCE_SECONDS = 300
STATUS_TTL_SECONDS = 600

logger = logging.getLogger("lyrpay")
_webhook_secret: str | None = None
_status: tuple[float, bool] | None = None


class CardError(ValueError):
    pass


# ── Availability ──────────────────────────────────────────────────────────────

def webhook_secret() -> str | None:
    global _webhook_secret
    if _webhook_secret is None:
        _webhook_secret = os.getenv("STRIPE_ISSUING_WEBHOOK_SECRET") or ""
        if not _webhook_secret:
            try:
                _webhook_secret = boto3.client("secretsmanager").get_secret_value(SecretId=WEBHOOK_SECRET_ID)["SecretString"].strip()
            except Exception:
                _webhook_secret = None  # look again next time
                return None
    return _webhook_secret or None


def issuing_live() -> bool:
    """Whether Stripe has switched Issuing on for the account and the approval webhook is set up; checked every 10 min."""
    global _status
    if _status and time.time() - _status[0] < STATUS_TTL_SECONDS:
        return _status[1]
    try:
        live = bool(stripe_client.secret_key()) and bool(webhook_secret()) and stripe_client.issuing_enabled()
    except stripe_client.StripeError:
        return _status[1] if _status else False
    _status = (time.time(), live)
    return live


# ── Signing up ────────────────────────────────────────────────────────────────

def clean_application(body: dict) -> dict:
    def text(key, limit=100):
        return re.sub(r"\s+", " ", str(body.get(key) or "")).strip()[:limit]

    first, last = text("firstName", 40), text("lastName", 40)
    if not first or not last:
        raise CardError("Enter your first and last name as they appear on your ID.")
    try:
        born = date.fromisoformat(text("dob", 10))
    except ValueError:
        raise CardError("Enter your date of birth.")
    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    if not 18 <= age <= 120:
        raise CardError("You need to be 18 or older for a Lyrpay card.")
    address = {"line1": text("line1"), "line2": text("line2"), "city": text("city", 60), "state": text("state", 40),
               "postal_code": text("postalCode", 20)}
    if not address["line1"] or not address["city"] or not address["postal_code"]:
        raise CardError("Enter your billing address: street, city and postal code.")
    if body.get("acceptTerms") is not True:
        raise CardError("Accept the card terms to continue.")
    return {"first": first, "last": last, "dob": {"day": born.day, "month": born.month, "year": born.year}, "address": address}


def issue_card(profile: dict, details: dict, ip: str, user_agent: str) -> dict:
    """Make the person a cardholder (once) and give them a virtual card in the platform's currency."""
    sub = profile["pk"].removeprefix("USER#")
    platform = stripe_client.platform()
    country = profile.get("country", "")
    if country != platform["country"]:
        raise CardError(f"Lyrpay cards are available to people living in {platform['country']} for now.")
    if any(c["status"] != "canceled" for c in cards_of(sub)):
        raise CardError("You already have a Lyrpay card.")
    cardholder = profile.get("cardholder")
    if not cardholder:
        cardholder = stripe_client.create_cardholder(
            user_sub=sub, name=f"{details['first']} {details['last']}", first_name=details["first"], last_name=details["last"],
            email=profile.get("email", ""), phone=profile.get("phone", ""),
            address={**details["address"], "country": country}, dob=details["dob"],
            ip=ip, user_agent=user_agent, accepted_at=int(time.time()),
        )["id"]
        ledger.update_profile(sub, cardholder=cardholder)
    card = stripe_client.create_card(user_sub=sub, cardholder=cardholder, currency=platform["currency"],
                                     per_authorization_limit=PER_AUTHORIZATION_LIMIT)
    item = {
        "pk": f"USER#{sub}", "sk": f"CARD#{card['id']}", "cardId": card["id"], "last4": card["last4"],
        "brand": card.get("brand", "Visa"), "expMonth": card["exp_month"], "expYear": card["exp_year"],
        "currency": card["currency"].upper(), "status": card["status"], "created": _now(),
        "name": f"{details['first']} {details['last']}",
    }
    table().put_item(Item=item)
    return public_card(item)


def cards_of(sub: str) -> list[dict]:
    items = table().query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("CARD#"))["Items"]
    return [public_card(i) for i in sorted(items, key=lambda i: i["created"], reverse=True)]


def public_card(item: dict) -> dict:
    return {k: (int(v) if k in ("expMonth", "expYear") else v) for k, v in item.items() if k not in ("pk", "sk")}


def card_of(sub: str, card_id: str) -> dict:
    item = ledger.get_item(f"USER#{sub}", f"CARD#{card_id}")
    if not item:
        raise CardError("You don't have that card.")
    return item


def set_frozen(sub: str, card_id: str, frozen: bool) -> dict:
    item = card_of(sub, card_id)
    if item["status"] == "canceled":
        raise CardError("That card is cancelled.")
    status = stripe_client.set_card_status(card_id, "inactive" if frozen else "active")["status"]
    table().update_item(Key={"pk": item["pk"], "sk": item["sk"]}, UpdateExpression="SET #s = :s",
                        ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": status})
    return public_card({**item, "status": status})


# ── Webhook ───────────────────────────────────────────────────────────────────

def verify(payload: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe's signature: t=<time>,v1=<hex hmac of "<time>.<payload>">."""
    pairs = [p.split("=", 1) for p in (header or "").split(",") if "=" in p]
    signatures = [v for k, v in pairs if k == "v1"]
    try:
        stamp = int(next((v for k, v in pairs if k == "t"), ""))
    except ValueError:
        return False
    if abs((now or time.time()) - stamp) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


def _owner(card: dict) -> str | None:
    """The Lyrpay account a Stripe card belongs to, checked against our own record of it."""
    sub = (card.get("metadata") or {}).get("user")
    return sub if sub and ledger.get_item(f"USER#{sub}", f"CARD#{card.get('id')}") else None


def _auth_key(auth_id: str) -> dict:
    return {"pk": f"CARDAUTH#{auth_id}", "sk": "AUTH"}


def _balance_change(sub: str, currency: str, amount: Decimal, must_cover: bool) -> dict:
    """Credit (amount > 0) or debit (amount < 0); a debit with must_cover fails rather than overdraw."""
    update = {
        "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": f"BAL#{currency}"},
        "UpdateExpression": "ADD amount :a SET currency = :c",
        "ExpressionAttributeValues": {":a": amount, ":c": currency},
    }
    if must_cover and amount < 0:
        update["ConditionExpression"] = "amount >= :need"
        update["ExpressionAttributeValues"][":need"] = -amount
    return {"Update": update}


def hold(auth: dict, force: bool = False) -> bool:
    """Approve and hold a new authorization if the balance covers it. force (Stripe approved it without asking us,
    after a timeout) holds it even if that overdraws. True when approved."""
    sub = _owner(auth.get("card") or {})
    if not sub:
        return False
    pending = auth.get("pending_request") or {}
    currency = (pending.get("currency") or auth.get("currency") or "").upper()
    amount = from_minor(int(pending.get("amount") if pending else auth.get("amount", 0)), currency)
    if amount <= 0:
        return True
    if ledger.get_item(f"CARDAUTH#{auth['id']}", "AUTH"):
        return True
    merchant = (auth.get("merchant_data") or {}).get("name") or "Card payment"
    tx = _tx_item(sub, "card", currency, -amount, counterparty=merchant, status="pending", reference=auth["id"],
                  card=(auth.get("card") or {}).get("last4", ""))
    record = {**_auth_key(auth["id"]), "sub": sub, "currency": currency, "held": amount, "covered": amount,
              "captured": Decimal(0), "txSk": tx["sk"], "merchant": merchant, "at": _now()}
    try:
        table().meta.client.transact_write_items(TransactItems=[
            _put(record, "attribute_not_exists(pk)"),
            _balance_change(sub, currency, -amount, must_cover=not force),
            _put(tx),
        ])
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        # Asked twice for the same authorization: keep the first answer
        return bool(ledger.get_item(f"CARDAUTH#{auth['id']}", "AUTH"))


def _settle(auth_id: str, captured_delta: Decimal = Decimal(0), close: bool = False, marker: dict | None = None):
    """Apply a capture and/or closing to an authorization's record and the balance, retrying if another event
    changed the record meanwhile."""
    for _ in range(5):
        record = ledger.get_item(f"CARDAUTH#{auth_id}", "AUTH")
        if not record:
            return False
        if close and record.get("closed") and not captured_delta:
            return True
        sub, currency = record["sub"], record["currency"]
        captured = record["captured"] + captured_delta
        closed = close or bool(record.get("closed"))
        # Cover what's been captured; while still open, keep at least the original hold
        covered = captured if closed else max(record["held"], captured)
        change = record["covered"] - covered
        status = ("paid" if captured > 0 else "failed") if closed else "pending"
        items = [{"Update": {
            "TableName": TABLE_NAME, "Key": _auth_key(auth_id),
            "UpdateExpression": "SET captured = :cap, covered = :cov, closed = :closed",
            "ConditionExpression": "covered = :old_cov AND captured = :old_cap",
            "ExpressionAttributeValues": {":cap": captured, ":cov": covered, ":closed": closed,
                                          ":old_cov": record["covered"], ":old_cap": record["captured"]},
        }}, {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": record["txSk"]},
            "UpdateExpression": "SET amount = :a, #s = :s",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": {":a": -max(covered, captured), ":s": status},
        }}]
        if change:
            items.append(_balance_change(sub, currency, change, must_cover=False))
        if marker:
            items.append(_put(marker, "attribute_not_exists(pk)"))
        try:
            table().meta.client.transact_write_items(TransactItems=items)
            return True
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if marker and ledger.get_item(marker["pk"], marker["sk"]):
                return True  # this Stripe transaction was already applied
    raise RuntimeError(f"Couldn't settle card authorization {auth_id}")


def apply_transaction(txn: dict):
    """A capture or refund Stripe made on a card, applied once."""
    marker = {"pk": f"CARDTXN#{txn['id']}", "sk": "TXN", "at": _now()}
    currency = txn["currency"].upper()
    amount = from_minor(abs(int(txn["amount"])), currency)
    auth_id = txn.get("authorization")
    if txn.get("type") == "capture" and auth_id and ledger.get_item(f"CARDAUTH#{auth_id}", "AUTH"):
        _settle(auth_id, captured_delta=amount, marker=marker)
        return
    sub = _owner(txn.get("card") if isinstance(txn.get("card"), dict) else {"id": txn.get("card"), "metadata": txn.get("metadata") or {}})
    if not sub:
        sub = (ledger.get_item(f"CARDAUTH#{auth_id}", "AUTH") or {}).get("sub") if auth_id else None
    if not sub:
        logger.warning("Card transaction %s has no Lyrpay owner", txn["id"])
        return
    merchant = (txn.get("merchant_data") or {}).get("name") or "Card"
    refund = txn.get("type") == "refund"
    tx = _tx_item(sub, "card", currency, amount if refund else -amount, counterparty=f"Refund · {merchant}" if refund else merchant,
                  status="paid", reference=txn["id"])
    try:
        table().meta.client.transact_write_items(TransactItems=[
            _put(marker, "attribute_not_exists(pk)"),
            _balance_change(sub, currency, amount if refund else -amount, must_cover=False),
            _put(tx),
        ])
    except ClientError as error:
        if error.response["Error"]["Code"] != "TransactionCanceledException":
            raise


def handle_event(event: dict) -> dict | None:
    """Act on a verified Stripe Issuing event; returns the answer for an authorization request."""
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    if kind == "issuing_authorization.request":
        return {"approved": hold(obj)}
    if kind == "issuing_authorization.created" and obj.get("approved"):
        # Normally already held by the request; this catches approvals Stripe made on a timeout
        hold(obj, force=True)
    elif kind == "issuing_authorization.updated" and obj.get("status") in ("closed", "reversed", "expired"):
        _settle(obj["id"], close=True)
    elif kind == "issuing_transaction.created":
        apply_transaction(obj)
    elif kind == "issuing_card.updated":
        sub = _owner(obj)
        if sub:
            table().update_item(Key={"pk": f"USER#{sub}", "sk": f"CARD#{obj['id']}"}, UpdateExpression="SET #s = :s",
                                ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": obj.get("status", "")})
    return None
