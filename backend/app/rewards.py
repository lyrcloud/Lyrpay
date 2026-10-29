"""Gift cards, promo codes (coupons), referrals and business accounts.

Gift cards are bought from a balance and redeemed into one, so they only move money people already hold. Promo codes
and referral bonuses add money Lyrpay pays for, so the admin sets them: a code gives a bonus on a top-up, within the
limits it was made with; a referral pays the bonuses in settings once the friend's first top-up is paid. Business
accounts are approved by the admin and then convert at the business fee and can pay many people at once.
"""
import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from app import ledger, rates
from app.ledger import TABLE_NAME, AlreadyApplied, InsufficientFunds, _credit, _debit, _now, _put, _transact, _tx_item, table
from app.money import quantize

# No 0/O or 1/I/L, so codes read back correctly over the phone (same alphabet as Lyrshop's gift cards)
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GIFT_MIN_USD, GIFT_MAX_USD = Decimal("1"), Decimal("2000")
COUPON_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,23}$")
COUPON_KINDS = ("percent", "fixed")
# A referral link counts for sign-ups within this many days of opening it, like Lyrtrip's
REFERRAL_DAYS = 30
# Each payment is three writes and a transaction takes 100, so a batch stays well inside one
BULK_MAX = 30
BUSINESS_STATUSES = ("pending", "approved", "rejected")
MONTHLY_VOLUMES = ("under-10k", "10k-100k", "100k-1m", "over-1m")

DEFAULT_SETTINGS = {"referrerBonusUsd": Decimal("5"), "friendBonusUsd": Decimal("0"),
                    "referralMinTopupUsd": Decimal("20"), "businessFeeBps": Decimal("25")}
SETTING_LIMITS = {"referrerBonusUsd": (0, 100), "friendBonusUsd": (0, 100), "referralMinTopupUsd": (0, 1000),
                  "businessFeeBps": (0, 200)}


class Conflict(Exception):
    """The request is fine but the state doesn't allow it (a used code, a business not yet approved): HTTP 409."""


def new_code(groups: int = 3, size: int = 4, prefix: str = "LYR") -> str:
    parts = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(size)) for _ in range(groups)]
    return "-".join([prefix, *parts]) if prefix else "".join(parts)


def gift_code(raw: str) -> str:
    """LYR-ABCD-EFGH-JKMN however it was typed: any case, with or without dashes."""
    letters = re.sub(r"[^A-Z0-9]", "", (raw or "").upper())
    if len(letters) == 15 and letters.startswith("LYR"):
        return "-".join(["LYR", letters[3:7], letters[7:11], letters[11:]])
    return letters


def masked(code: str) -> str:
    return f"{code[:4]}…{code[-4:]}" if code else ""


def _decimal(raw, message: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError(message)
    if not value.is_finite():
        raise ValueError(message)
    return value


def usd_value(amount: Decimal, currency: str) -> Decimal:
    return amount / rates.rate("USD", currency) if currency != "USD" else amount


def from_usd(usd: Decimal, currency: str) -> Decimal:
    return quantize(usd * rates.rate("USD", currency), currency)


# ── Settings ──────────────────────────────────────────────────────────────────

def settings() -> dict:
    stored = ledger.get_item("SETTINGS", "REWARDS") or {}
    return {k: Decimal(str(stored.get(k, default))) for k, default in DEFAULT_SETTINGS.items()}


def public_settings(values: dict) -> dict:
    return {k: (int(v) if k == "businessFeeBps" else str(v)) for k, v in values.items()}


def save_settings(body: dict, admin_email: str) -> dict:
    values = settings()
    for key, (low, high) in SETTING_LIMITS.items():
        if body.get(key) in (None, ""):
            continue
        value = _decimal(body[key], "Enter numbers for the rewards settings.")
        if not low <= value <= high:
            raise ValueError(f"{key} must be {low} to {high}.")
        values[key] = value.quantize(Decimal("1")) if key == "businessFeeBps" else value.quantize(Decimal("0.01"))
    table().put_item(Item={"pk": "SETTINGS", "sk": "REWARDS", **values, "updatedBy": admin_email, "updated": _now()})
    return values


# ── Gift cards ────────────────────────────────────────────────────────────────

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_gift_card(body: dict) -> dict:
    to_email = str(body.get("toEmail") or "").strip().lower()
    if to_email and not EMAIL.match(to_email):
        raise ValueError("Enter a valid email for the recipient, or leave it empty to share the code yourself.")
    return {
        "toEmail": to_email,
        "toName": re.sub(r"\s+", " ", str(body.get("toName") or "")).strip()[:60],
        "message": str(body.get("message") or "").strip()[:300],
    }


def buy_gift_card(sub: str, sender_name: str, currency: str, amount: Decimal, details: dict) -> dict:
    usd = usd_value(amount, currency)
    if not GIFT_MIN_USD <= usd <= GIFT_MAX_USD:
        raise ValueError(f"Gift cards are worth ${GIFT_MIN_USD} to ${GIFT_MAX_USD:,} (or the same in another currency).")
    code, at = new_code(), _now()
    card = {
        "pk": f"GIFT#{code}", "sk": "GIFT", "code": code, "currency": currency, "amount": amount, "status": "active",
        "fromSub": sub, "fromName": sender_name, "created": at, **{k: v for k, v in details.items() if v},
    }
    _transact([
        _debit(sub, currency, amount),
        _put(card, "attribute_not_exists(pk)"),
        _put({"pk": f"USER#{sub}", "sk": f"GIFTSENT#{at}#{code}", "code": code}),
        _put(_tx_item(sub, "giftcard", currency, -amount, counterparty=details.get("toName") or details.get("toEmail") or "Gift card",
                      reference=masked(code))),
    ], InsufficientFunds())
    return card


def public_gift_card(card: dict) -> dict:
    shown = {k: card.get(k, "") for k in ("code", "currency", "status", "toEmail", "toName", "message", "created",
                                           "redeemedAt", "fromName")}
    shown["amount"] = str(quantize(card["amount"], card["currency"]))
    # The code only matters while it can still be redeemed
    if card.get("status") != "active":
        shown["code"] = masked(card["code"])
    return shown


def gift_cards_bought(sub: str, limit: int = 30) -> list[dict]:
    pointers = table().query(
        KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("GIFTSENT#"),
        ScanIndexForward=False, Limit=limit,
    )["Items"]
    if not pointers:
        return []
    keys = [{"pk": f"GIFT#{p['code']}", "sk": "GIFT"} for p in pointers]
    found = table().meta.client.batch_get_item(RequestItems={TABLE_NAME: {"Keys": keys, "ConsistentRead": True}})
    cards = {c["code"]: c for c in found["Responses"].get(TABLE_NAME, [])}
    return [public_gift_card(cards[p["code"]]) for p in pointers if p["code"] in cards]


def _close_gift(code: str, status: str, by: str, extra_condition: str = "", values: dict | None = None) -> dict:
    return {"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"GIFT#{code}", "sk": "GIFT"},
        "UpdateExpression": "SET #s = :closed, closedBy = :by, redeemedAt = :at",
        "ConditionExpression": "#s = :active" + extra_condition,
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": {":closed": status, ":by": by, ":at": _now(), ":active": "active", **(values or {})},
    }}


def redeem_gift_card(sub: str, raw_code: str) -> dict:
    code = gift_code(raw_code)
    card = ledger.get_item(f"GIFT#{code}", "GIFT") if code else None
    if not card or card.get("status") != "active":
        raise Conflict("That gift card code isn't valid or has already been used.")
    _transact([
        _close_gift(code, "redeemed", sub),
        _credit(sub, card["currency"], card["amount"]),
        _put(_tx_item(sub, "redeem", card["currency"], card["amount"], counterparty=f"Gift card from {card.get('fromName') or 'a friend'}",
                      reference=masked(code), note=card.get("message", "")[:140])),
    ], Conflict("That gift card has just been used."))
    return card


def cancel_gift_card(sub: str, raw_code: str) -> dict:
    """Put an unused card's money back on the buyer's balance."""
    code = gift_code(raw_code)
    card = ledger.get_item(f"GIFT#{code}", "GIFT") if code else None
    if not card or card.get("fromSub") != sub:
        raise Conflict("You don't have a gift card with that code.")
    if card.get("status") != "active":
        raise Conflict("That gift card has already been used or cancelled.")
    _transact([
        _close_gift(code, "cancelled", sub, " AND fromSub = :me", {":me": sub}),
        _credit(sub, card["currency"], card["amount"]),
        _put(_tx_item(sub, "refund", card["currency"], card["amount"], counterparty="Cancelled gift card", reference=masked(code))),
    ], Conflict("That gift card has just been used."))
    return card


# ── Promo codes ───────────────────────────────────────────────────────────────

def coupon_code(raw: str) -> str:
    return re.sub(r"[^A-Z0-9_-]", "", (raw or "").upper())[:24]


def clean_coupon(body: dict) -> dict:
    code = coupon_code(body.get("code"))
    if not COUPON_CODE.match(code):
        raise ValueError("Codes are 3 to 24 letters, numbers, dashes or underscores.")
    kind = body.get("kind")
    if kind not in COUPON_KINDS:
        raise ValueError("Choose a percentage bonus or a fixed bonus.")
    value = _decimal(body.get("value"), "Enter the bonus.")
    if kind == "percent" and not 1 <= value <= 50:
        raise ValueError("A percentage bonus must be 1 to 50%.")
    if kind == "fixed" and not Decimal("0.5") <= value <= 500:
        raise ValueError("A fixed bonus must be $0.50 to $500.")
    max_bonus = _decimal(body.get("maxBonusUsd") or 50, "Enter the largest bonus.")
    min_topup = _decimal(body.get("minTopupUsd") or 0, "Enter the smallest top-up.")
    try:
        max_uses = int(body.get("maxUses") or 0)
    except (TypeError, ValueError):
        raise ValueError("Enter a whole number for the usage limit.")
    if max_bonus <= 0 or min_topup < 0 or max_uses < 0:
        raise ValueError("The limits can't be negative.")
    expires = str(body.get("expires") or "").strip()
    if expires:
        try:
            date.fromisoformat(expires)
        except ValueError:
            raise ValueError("Choose a valid expiry date.")
    return {
        "code": code, "kind": kind, "value": value.quantize(Decimal("0.01")),
        "maxBonusUsd": max_bonus.quantize(Decimal("0.01")), "minTopupUsd": min_topup.quantize(Decimal("0.01")),
        "maxUses": max_uses, "oncePerPerson": body.get("oncePerPerson", True) is not False, "expires": expires,
        "active": body.get("active", True) is not False,
        "description": re.sub(r"\s+", " ", str(body.get("description") or "")).strip()[:120],
    }


def create_coupon(fields: dict, admin_email: str) -> dict:
    item = {"pk": f"COUPON#{fields['code']}", "sk": "COUPON", **fields, "uses": 0, "createdBy": admin_email, "created": _now()}
    try:
        table().put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("A promo code with that name already exists.")
        raise
    return item


def set_coupon_active(code: str, active: bool) -> dict:
    try:
        return table().update_item(
            Key={"pk": f"COUPON#{coupon_code(code)}", "sk": "COUPON"}, UpdateExpression="SET active = :a",
            ConditionExpression="attribute_exists(pk)", ExpressionAttributeValues={":a": active}, ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("There's no promo code with that name.")
        raise


def public_coupon(item: dict) -> dict:
    shown = {k: v for k, v in item.items() if k not in ("pk", "sk")}
    for key in ("value", "maxBonusUsd", "minTopupUsd"):
        shown[key] = str(shown[key])
    shown["uses"], shown["maxUses"] = int(shown["uses"]), int(shown["maxUses"])
    return shown


def coupons() -> list[dict]:
    items = ledger._scan(FilterExpression=Attr("sk").eq("COUPON"))
    return sorted((public_coupon(i) for i in items), key=lambda c: c.get("created", ""), reverse=True)


def coupon_bonus(raw_code: str, sub: str, currency: str, amount: Decimal) -> tuple[dict, Decimal]:
    """The promo code and the bonus it gives on this top-up, or ValueError saying why it can't be used."""
    code = coupon_code(raw_code)
    coupon = ledger.get_item(f"COUPON#{code}", "COUPON") if code else None
    if not coupon or not coupon.get("active"):
        raise ValueError("That promo code isn't valid.")
    if coupon.get("expires") and coupon["expires"] < datetime.now(timezone.utc).date().isoformat():
        raise ValueError("That promo code has expired.")
    if coupon["maxUses"] and coupon["uses"] >= coupon["maxUses"]:
        raise ValueError("That promo code has been fully used.")
    if coupon.get("oncePerPerson") and ledger.get_item(f"COUPON#{code}", f"USED#{sub}"):
        raise ValueError("You've already used that promo code.")
    try:
        if usd_value(amount, currency) < coupon["minTopupUsd"]:
            raise ValueError(f"That promo code needs a top-up of at least ${coupon['minTopupUsd']} (or the same in {currency}).")
        cap = from_usd(coupon["maxBonusUsd"], currency)
        bonus = quantize(amount * coupon["value"] / 100, currency) if coupon["kind"] == "percent" else from_usd(coupon["value"], currency)
    except KeyError:
        raise ValueError(f"Promo codes can't be used with {currency} yet.")
    bonus = min(bonus, cap)
    if bonus <= 0:
        raise ValueError("That top-up is too small for a bonus.")
    return coupon, bonus


def coupon_writes(coupon: dict, sub: str, session_id: str, currency: str, bonus: Decimal) -> list:
    """The writes that spend one use of a promo code and record its bonus, for ledger.credit_topup's transaction
    (which adds the bonus to the top-up's own balance update, since one transaction can't update a balance twice)."""
    code = coupon["code"]
    items = [{"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"COUPON#{code}", "sk": "COUPON"},
        "UpdateExpression": "ADD uses :one",
        "ConditionExpression": "active = :yes AND (maxUses = :zero OR uses < maxUses)",
        "ExpressionAttributeValues": {":one": 1, ":yes": True, ":zero": 0},
    }}]
    if coupon.get("oncePerPerson"):
        items.append(_put({"pk": f"COUPON#{code}", "sk": f"USED#{sub}", "at": _now(), "session": session_id}, "attribute_not_exists(pk)"))
    items.append(_put(_tx_item(sub, "bonus", currency, bonus, counterparty=f"Promo code {code}", reference=session_id)))
    return items


# ── Referrals ─────────────────────────────────────────────────────────────────

def referral_code(profile: dict) -> str:
    """The person's own referral code, made the first time they ask for it."""
    if profile.get("referralCode"):
        return profile["referralCode"]
    sub = profile["pk"].removeprefix("USER#")
    for _ in range(5):
        code = new_code(groups=2, size=4, prefix="")
        try:
            table().put_item(Item={"pk": f"REF#{code}", "sk": "REF", "sub": sub}, ConditionExpression="attribute_not_exists(pk)")
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                continue
            raise
        try:
            table().update_item(
                Key={"pk": profile["pk"], "sk": "PROFILE"}, UpdateExpression="SET referralCode = :c",
                ConditionExpression="attribute_not_exists(referralCode)", ExpressionAttributeValues={":c": code},
            )
        except ClientError as error:
            # Another request made one first: keep theirs
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            table().delete_item(Key={"pk": f"REF#{code}", "sk": "REF"})
            return ledger.get_item(profile["pk"], "PROFILE")["referralCode"]
        profile["referralCode"] = code
        return code
    raise RuntimeError("Couldn't make a referral code")


def claim_referral(profile: dict, raw_code: str) -> str:
    """Record who referred this person; only for new wallets that haven't been referred or topped up yet."""
    code = re.sub(r"[^A-Z0-9]", "", (raw_code or "").upper())[:12]
    ref = ledger.get_item(f"REF#{code}", "REF") if code else None
    sub = profile["pk"].removeprefix("USER#")
    if not ref:
        raise ValueError("That referral link isn't valid.")
    if ref["sub"] == sub:
        raise ValueError("You can't refer yourself.")
    if profile.get("referredBy"):
        raise Conflict("You've already been referred.")
    created = datetime.fromisoformat(profile.get("created") or _now())
    if (datetime.now(timezone.utc) - created).days > REFERRAL_DAYS or any(tx["kind"] == "topup" for tx in ledger.transactions(sub)):
        raise Conflict("Referral links are for new Lyrpay wallets.")
    _transact([
        {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": profile["pk"], "sk": "PROFILE"},
            "UpdateExpression": "SET referredBy = :r, referredAt = :at",
            "ConditionExpression": "attribute_not_exists(referredBy)",
            "ExpressionAttributeValues": {":r": ref["sub"], ":at": _now()},
        }},
        {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": f"USER#{ref['sub']}", "sk": "PROFILE"},
            "UpdateExpression": "ADD referralSignups :one", "ExpressionAttributeValues": {":one": 1},
        }},
    ], Conflict("You've already been referred."))
    referrer = ledger.get_item(f"USER#{ref['sub']}", "PROFILE") or {}
    return referrer.get("name", "")


def reward_referral(sub: str, currency: str, amount: Decimal) -> bool:
    """Once a referred friend's first qualifying top-up is paid, pay the referral bonuses (once). True if paid now."""
    profile = ledger.get_item(f"USER#{sub}", "PROFILE") or {}
    referrer_sub = profile.get("referredBy")
    if not referrer_sub or profile.get("referralRewarded"):
        return False
    values = settings()
    try:
        if usd_value(amount, currency) < values["referralMinTopupUsd"]:
            return False
    except KeyError:
        return False
    referrer = ledger.get_item(f"USER#{referrer_sub}", "PROFILE") or {}
    to_referrer, to_friend = values["referrerBonusUsd"], values["friendBonusUsd"]
    items = [{"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": "PROFILE"},
        "UpdateExpression": "SET referralRewarded = :at",
        "ConditionExpression": "attribute_not_exists(referralRewarded)",
        "ExpressionAttributeValues": {":at": _now()},
    }}, {"Update": {
        "TableName": TABLE_NAME, "Key": {"pk": f"USER#{referrer_sub}", "sk": "PROFILE"},
        "UpdateExpression": "ADD referralCount :one, referralEarnedUsd :b",
        "ExpressionAttributeValues": {":one": 1, ":b": to_referrer},
    }}]
    if to_referrer > 0:
        items += [_credit(referrer_sub, "USD", to_referrer),
                  _put(_tx_item(referrer_sub, "bonus", "USD", to_referrer, counterparty=f"Referral · {profile.get('name') or 'a friend'}"))]
    if to_friend > 0:
        items += [_credit(sub, "USD", to_friend),
                  _put(_tx_item(sub, "bonus", "USD", to_friend, counterparty=f"Welcome bonus · invited by {referrer.get('name') or 'a friend'}"))]
    try:
        _transact(items, AlreadyApplied())
    except AlreadyApplied:
        return False
    return True


def referral_summary(profile: dict, site_url: str) -> dict:
    code = referral_code(profile)
    values = settings()
    return {
        "code": code, "link": f"{site_url}/?ref={code}",
        "signups": int(profile.get("referralSignups", 0)), "rewarded": int(profile.get("referralCount", 0)),
        "earnedUsd": str(profile.get("referralEarnedUsd", 0)),
        "referrerBonusUsd": str(values["referrerBonusUsd"]), "friendBonusUsd": str(values["friendBonusUsd"]),
        "minTopupUsd": str(values["referralMinTopupUsd"]),
    }


# ── Business accounts ─────────────────────────────────────────────────────────

def clean_business(body: dict) -> dict:
    def text(key, limit):
        return re.sub(r"\s+", " ", str(body.get(key) or "")).strip()[:limit]

    company, registration = text("company", 100), text("registrationId", 40)
    country = text("country", 2).upper()
    website, volume, about = text("website", 200), body.get("monthlyVolume"), text("about", 400)
    if len(company) < 2:
        raise ValueError("Enter your company's registered name.")
    if not re.match(r"^[A-Z]{2}$", country):
        raise ValueError("Choose the country your company is registered in.")
    if not re.match(r"^[A-Za-z0-9 .\-/]{3,40}$", registration):
        raise ValueError("Enter your company registration or tax number.")
    if website and not re.match(r"^(https?://)?[^\s.]+\.[^\s]{2,}$", website):
        raise ValueError("Enter a valid website, or leave it empty.")
    if volume not in MONTHLY_VOLUMES:
        raise ValueError("Choose your expected monthly volume.")
    return {"company": company, "country": country, "registrationId": registration, "website": website,
            "monthlyVolume": volume, "about": about}


def apply_business(profile: dict, fields: dict) -> dict:
    current = profile.get("business") or {}
    if current.get("status") in ("pending", "approved"):
        raise Conflict("Your business application is already " + ("approved." if current["status"] == "approved" else "being reviewed."))
    business = {**fields, "status": "pending", "applied": _now()}
    table().update_item(Key={"pk": profile["pk"], "sk": "PROFILE"}, UpdateExpression="SET business = :b",
                        ExpressionAttributeValues={":b": business})
    return business


def review_business(sub: str, status: str, note: str, reviewer: str) -> dict:
    if status not in ("approved", "rejected"):
        raise ValueError("Approve or reject the application.")
    try:
        return table().update_item(
            Key={"pk": f"USER#{sub}", "sk": "PROFILE"},
            UpdateExpression="SET business.#s = :s, business.reviewed = :at, business.reviewedBy = :by, business.note = :n",
            ConditionExpression="attribute_exists(business)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": status, ":at": _now(), ":by": reviewer, ":n": note.strip()[:300]},
            ReturnValues="ALL_NEW",
        )["Attributes"]
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("That person hasn't applied for a business account.")
        raise


def is_business(profile: dict) -> bool:
    return (profile.get("business") or {}).get("status") == "approved"


def fee_bps(profile: dict | None, standard: Decimal) -> Decimal:
    return min(standard, settings()["businessFeeBps"]) if profile and is_business(profile) else standard


def payer_name(profile: dict) -> str:
    """What recipients see: the company for an approved business, else the person's name."""
    return profile["business"]["company"] if is_business(profile) else profile.get("name", "")


def public_business(business: dict | None) -> dict | None:
    if not business:
        return None
    return {k: business.get(k, "") for k in ("company", "country", "registrationId", "website", "monthlyVolume", "about",
                                              "status", "applied", "reviewed", "note")}
