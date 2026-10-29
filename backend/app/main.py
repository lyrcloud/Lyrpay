import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app import ledger, mail, rates, rewards, stripe_client
from app.auth import User, current_user
from app.money import exponent, from_minor, parse_amount, quantize

# Spread on currency conversions, in basis points (50 = 0.5%)
FX_FEE_BPS = Decimal(os.getenv("FX_FEE_BPS", "50"))
ALLOWED_ORIGINS = [o for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o]
# Super admins: verified emails that can open the admin overview
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "lyrclouds@gmail.com").split(",") if e.strip()}
CURRENCY = re.compile(r"^[A-Z]{3}$")
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")

logger = logging.getLogger("lyrpay")

app = FastAPI(title="Lyrpay API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "PUT"],
    allow_headers=["authorization", "content-type"],
)


@app.exception_handler(ValueError)
def value_error(_: Request, error: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(rewards.Conflict)
def conflict(_: Request, error: rewards.Conflict):
    return JSONResponse(status_code=409, content={"detail": str(error)})


def currency_code(raw: str) -> str:
    code = (raw or "").upper()
    if not CURRENCY.match(code):
        raise ValueError("Choose a currency.")
    return code


def money(amount: Decimal, currency: str) -> str:
    return str(quantize(amount, currency))


def statement(sub: str) -> dict:
    return {
        "balances": {c: money(a, c) for c, a in sorted(ledger.balances(sub).items())},
        "transactions": [
            {k: (str(v) if isinstance(v, Decimal) else v) for k, v in tx.items()}
            for tx in ledger.transactions(sub)
        ],
    }


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "lyrpay"}


@app.get("/api/config")
def config():
    return {"cardPayments": bool(stripe_client.secret_key()), "fxFeeBps": int(FX_FEE_BPS)}


PHONE_AUTH_URL = os.getenv("PHONE_AUTH_URL", "https://73j9469lxd.execute-api.us-east-1.amazonaws.com").rstrip("/")


@app.post("/api/auth/phone/{action}")
async def phone_auth(action: str, request: Request):
    """Mobile sign-up and password resets for the shared Lyr account pool: Lyrtrip's API texts and checks
    the code (Twilio Verify) and creates the account. Relayed here because that API only takes Lyrtrip's origins."""
    if action not in ("send", "register", "reset"):
        raise HTTPException(status_code=400, detail="Unknown request.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{PHONE_AUTH_URL}/api/auth/phone/{action}",
            content=await request.body(), headers={"content-type": "application/json"},
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=body.get("error") or "Mobile sign-in is unavailable right now.")
    return {"success": True}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    settle_withdrawals(user.sub, profile)
    try:
        mail.welcome(profile)
    except Exception:
        logger.exception("Welcome email for %s failed", user.sub)
    return {
        "profile": {k: profile.get(k, "") for k in ("name", "email", "phone", "currency", "country")},
        "admin": is_admin(user),
        "business": rewards.public_business(profile.get("business")),
        **statement(user.sub),
    }


def is_admin(user: User) -> bool:
    # user.email is only set when the email is verified, so nobody can claim an admin address
    return bool(user.email) and user.email in ADMIN_EMAILS


def admin_user(user: User = Depends(current_user)) -> User:
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Only Lyrpay admins can see this.")
    return user


SECOND_HALF = ("receive", "redeem", "refund")


@app.get("/api/admin/overview")
def admin_overview(days: int = 14, _: User = Depends(admin_user)):
    """Everyone's accounts, money held and activity, valued in US dollars at today's rates."""
    days = max(1, min(days, 90))
    usd = rates.usd_rates()["rates"]

    def in_usd(amount, currency: str) -> float:
        return float(Decimal(str(amount)) / Decimal(str(usd[currency]))) if currency in usd else 0.0

    data = ledger.everything()
    names = {p["pk"]: p.get("name") or p.get("email") or p.get("phone") or "" for p in data["profiles"]}

    held: dict[str, Decimal] = {}
    held_by_user: dict[str, float] = {}
    for item in data["balances"]:
        currency, amount = item["currency"], item["amount"]
        if amount:
            held[currency] = held.get(currency, Decimal(0)) + amount
            held_by_user[item["pk"]] = held_by_user.get(item["pk"], 0.0) + in_usd(amount, currency)

    today = datetime.now(timezone.utc).date()
    daily = {(today - timedelta(days=i)).isoformat(): {"signups": 0, "volumeUsd": 0.0, "count": 0} for i in range(days)}
    by_kind: dict[str, dict] = {}
    pending = 0
    for tx in data["transactions"]:
        kind, value = tx["kind"], abs(in_usd(tx["amount"], tx["currency"]))
        bucket = by_kind.setdefault(kind, {"count": 0, "volumeUsd": 0.0})
        bucket["count"] += 1
        bucket["volumeUsd"] += value
        day = daily.get(tx["at"][:10])
        # A transfer is two lines (send and receive), and a gift card is bought then redeemed; count the money once
        if day and kind not in SECOND_HALF:
            day["count"] += 1
            day["volumeUsd"] += value
        pending += tx.get("status") == "pending"
    for profile in data["profiles"]:
        day = daily.get(str(profile.get("created", ""))[:10])
        if day:
            day["signups"] += 1

    recent = sorted(data["transactions"], key=lambda tx: tx["at"], reverse=True)[:30]
    users = sorted(data["profiles"], key=lambda p: str(p.get("created", "")), reverse=True)
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "users": len(users),
            "newUsers": sum(d["signups"] for d in daily.values()),
            "heldUsd": round(sum(in_usd(a, c) for c, a in held.items()), 2),
            "transactions": len(data["transactions"]),
            "volumeUsd": round(sum(b["volumeUsd"] for k, b in by_kind.items() if k not in SECOND_HALF), 2),
            "pendingWithdrawals": pending,
            "unsubscribed": len(data["optouts"]),
            "giftCardsOutstandingUsd": round(sum(in_usd(g["amount"], g["currency"]) for g in data["gifts"] if g.get("status") == "active"), 2),
            "giftCardsSold": len(data["gifts"]),
            "activeCoupons": sum(1 for c in data["coupons"] if c.get("active")),
            "referrals": sum(1 for p in data["profiles"] if p.get("referredBy")),
            "businesses": sum(1 for p in data["profiles"] if rewards.is_business(p)),
            "businessApplications": sum(1 for p in data["profiles"] if (p.get("business") or {}).get("status") == "pending"),
        },
        "held": sorted(
            ({"currency": c, "amount": money(a, c), "usd": round(in_usd(a, c), 2)} for c, a in held.items()),
            key=lambda h: h["usd"], reverse=True,
        ),
        "byKind": {k: {"count": b["count"], "volumeUsd": round(b["volumeUsd"], 2)} for k, b in by_kind.items()},
        "daily": [{"date": d, **v, "volumeUsd": round(v["volumeUsd"], 2)} for d, v in sorted(daily.items())],
        "users": [
            {
                "name": p.get("name", ""), "email": p.get("email", ""), "phone": p.get("phone", ""),
                "country": p.get("country", ""), "currency": p.get("currency", ""), "created": p.get("created", ""),
                "heldUsd": round(held_by_user.get(p["pk"], 0.0), 2), "payouts": bool(p.get("payoutAccount")),
                "business": (p.get("business") or {}).get("status", ""), "referred": bool(p.get("referredBy")),
            }
            for p in users
        ],
        "recent": [
            {
                "id": tx["id"], "at": tx["at"], "kind": tx["kind"], "currency": tx["currency"], "amount": str(tx["amount"]),
                "status": tx.get("status", ""), "user": names.get(tx["pk"], ""), "counterparty": tx.get("counterparty", ""),
            }
            for tx in recent
        ],
    }


class ProfileUpdate(BaseModel):
    currency: str | None = None
    country: str | None = None


@app.patch("/api/me")
def update_me(body: ProfileUpdate, user: User = Depends(current_user)):
    country = body.country.upper() if body.country else None
    if country and not re.match(r"^[A-Z]{2}$", country):
        raise ValueError("Choose a country.")
    profile = ledger.update_profile(
        user.sub, currency=currency_code(body.currency) if body.currency else None, country=country,
    )
    return {"profile": {k: profile.get(k, "") for k in ("name", "email", "phone", "currency", "country")}}


UNSUBSCRIBED_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unsubscribed · Lyrpay</title></head><body style="margin:0;background:#f5f6fb;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif">
<div style="max-width:480px;margin:48px auto;padding:28px;background:#fff;border-radius:14px;color:#1f2937">
<a href="/" style="color:#5b5bf6;font-size:24px;font-weight:800;text-decoration:none">Lyrpay</a>
<h1 style="font-size:20px">{heading}</h1><p style="line-height:1.55">{body}</p></div></body></html>"""


@app.api_route("/api/unsubscribe", methods=["GET", "POST"], response_class=HTMLResponse)
def unsubscribe(t: str = ""):
    """The link in every Lyrpay email; POST is the one-click unsubscribe mail apps send."""
    email = mail.read_unsubscribe_token(t)
    if not email:
        return HTMLResponse(UNSUBSCRIBED_PAGE.format(
            heading="That link isn't valid", body="Reply to any Lyrpay email and we'll take you off the list."), 400)
    ledger.opt_out(email)
    return UNSUBSCRIBED_PAGE.format(
        heading="You're unsubscribed", body="Lyrpay won't email you any more.")


@app.get("/api/rates")
def fx_rates(base: str = "USD"):
    base = currency_code(base)
    data = rates.usd_rates()
    if base not in data["rates"]:
        raise ValueError(f"{base} isn't supported yet.")
    pivot = Decimal(str(data["rates"][base]))
    return {
        "base": base, "updated": data["updated"],
        "rates": {c: float(Decimal(str(r)) / pivot) for c, r in data["rates"].items()},
    }


@app.get("/api/crypto/markets")
def crypto(vs: str = "usd"):
    return {"vs": vs.upper(), "coins": rates.crypto_markets(vs)}


class TopUp(BaseModel):
    amount: str
    currency: str
    coupon: str = ""


@app.post("/api/topup")
def topup(body: TopUp, request: Request, user: User = Depends(current_user)):
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    coupon = ""
    if body.coupon.strip():
        coupon = rewards.coupon_bonus(body.coupon, user.sub, currency, amount)[0]["code"]
    session = stripe_call(
        stripe_client.create_checkout,
        user_sub=user.sub, email=user.email, amount=amount, currency=currency, origin=site_origin(request), coupon=coupon,
    )
    return {"url": session["url"]}


@app.get("/api/coupons/check")
def coupon_check(code: str, amount: str, currency: str, user: User = Depends(current_user)):
    """The bonus a promo code would add to this top-up."""
    currency = currency_code(currency)
    coupon, bonus = rewards.coupon_bonus(code, user.sub, currency, parse_amount(amount, currency))
    return {"code": coupon["code"], "bonus": money(bonus, currency), "currency": currency, "description": coupon.get("description", "")}


class TopUpConfirm(BaseModel):
    sessionId: str


@app.post("/api/topup/confirm")
def topup_confirm(body: TopUpConfirm, user: User = Depends(current_user)):
    if not re.match(r"^cs_[A-Za-z0-9_]+$", body.sessionId):
        raise ValueError("That payment link isn't valid.")
    session = stripe_call(stripe_client.get_checkout, body.sessionId)
    if session.get("client_reference_id") != user.sub:
        raise HTTPException(status_code=409, detail="That payment belongs to another account.")
    if session.get("payment_status") != "paid":
        raise HTTPException(status_code=409, detail="That payment hasn't completed yet.")
    currency = session["currency"].upper()
    amount = from_minor(session["amount_total"], currency)
    # A promo code chosen at checkout is checked again now: it may have run out since
    extras, bonus, coupon_note = [], Decimal(0), ""
    code = (session.get("metadata") or {}).get("coupon")
    if code:
        try:
            coupon, bonus = rewards.coupon_bonus(code, user.sub, currency, amount)
            extras = rewards.coupon_writes(coupon, user.sub, body.sessionId, currency, bonus)
        except ValueError as error:
            coupon_note = str(error)
    credited = True
    try:
        if not ledger.credit_topup(user.sub, body.sessionId, currency, amount, bonus, extras) and extras:
            bonus, coupon_note = Decimal(0), "That promo code ran out before your payment finished."
    except ledger.AlreadyApplied:
        credited, bonus, coupon_note = False, Decimal(0), ""
    # Safe to repeat: the referral bonus is paid at most once per referred person
    rewards.reward_referral(user.sub, currency, amount)
    return {
        "credited": credited, "currency": currency, "amount": money(amount, currency),
        "bonus": money(bonus, currency), "couponNote": coupon_note, **statement(user.sub),
    }


def conversion(source: str, target: str, amount: Decimal, fee_bps: Decimal = FX_FEE_BPS) -> tuple[Decimal, Decimal]:
    try:
        mid = rates.rate(source, target)
    except KeyError as error:
        raise ValueError(str(error).strip("'\""))
    applied = mid * (1 - fee_bps / 10000)
    return applied, quantize(amount * applied, target)


def optional_user(authorization: str = Header(default="")) -> User | None:
    try:
        return current_user(authorization) if authorization else None
    except HTTPException:
        return None


def fee_for(user: User | None) -> Decimal:
    """Business accounts convert at the business fee; everyone else at FX_FEE_BPS."""
    profile = ledger.get_item(f"USER#{user.sub}", "PROFILE") if user else None
    return rewards.fee_bps(profile, FX_FEE_BPS)


@app.get("/api/convert/quote")
def convert_quote(source: str, target: str, amount: str, user: User | None = Depends(optional_user)):
    source, target = currency_code(source), currency_code(target)
    value = parse_amount(amount, source)
    fee = fee_for(user)
    applied, received = conversion(source, target, value, fee)
    return {"rate": str(applied), "receive": money(received, target), "feeBps": int(fee)}


class Convert(BaseModel):
    source: str
    target: str
    amount: str


@app.post("/api/convert")
def convert(body: Convert, user: User = Depends(current_user)):
    source, target = currency_code(body.source), currency_code(body.target)
    if source == target:
        raise ValueError("Pick two different currencies.")
    amount = parse_amount(body.amount, source)
    applied, received = conversion(source, target, amount, fee_for(user))
    if received <= 0:
        raise ValueError("That amount is too small to convert.")
    try:
        ledger.convert(user.sub, source, amount, target, received, applied)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {source}.")
    return {"receive": money(received, target), **statement(user.sub)}


def handle_of(raw: str) -> str:
    raw = (raw or "").strip()
    if "@" in raw:
        return raw.lower()
    phone = re.sub(r"[\s().-]", "", raw)
    if not PHONE.match(phone):
        raise ValueError("Enter their email or mobile number with the country code, like +1 415 555 2671.")
    return phone


@app.get("/api/recipient")
def recipient(q: str, user: User = Depends(current_user)):
    found = ledger.find_recipient(handle_of(q))
    if not found:
        raise HTTPException(status_code=409, detail="No Lyrpay account uses that email or number yet.")
    if found["sub"] == user.sub:
        raise ValueError("That's you.")
    return {"name": found["name"]}


class Transfer(BaseModel):
    to: str
    amount: str
    currency: str
    note: str = ""


@app.post("/api/transfer")
def transfer(body: Transfer, user: User = Depends(current_user)):
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    found = ledger.find_recipient(handle_of(body.to))
    if not found:
        raise HTTPException(status_code=409, detail="No Lyrpay account uses that email or number yet.")
    if found["sub"] == user.sub:
        raise ValueError("That's you.")
    profile = ledger.ensure_profile(user)
    try:
        ledger.transfer(user, rewards.payer_name(profile), found, currency, amount, body.note.strip()[:140])
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {currency}.")
    return {"sentTo": found["name"], **statement(user.sub)}


@app.get("/api/currencies")
def currencies():
    return {"currencies": {c: exponent(c) for c in sorted(rates.usd_rates()["rates"])}}


def site_origin(request: Request) -> str:
    origin = request.headers.get("origin") or ""
    return origin if origin in ALLOWED_ORIGINS else ALLOWED_ORIGINS[0]


def stripe_call(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except stripe_client.StripeError as error:
        raise HTTPException(status_code=502, detail=str(error))


def send_withdrawal(tx: dict, account_id: str) -> str:
    """Make sure Stripe has the transfer for a pending withdrawal and record the outcome:
    'paid', 'failed' (balance restored) or 'pending' (Stripe's answer is still unknown)."""
    try:
        transfer = stripe_client.find_transfer(account_id, tx["id"]) or stripe_client.transfer_to(
            account_id, -tx["amount"], tx["currency"], tx["id"],
        )
    except stripe_client.StripeError as error:
        if not error.definitive:
            return "pending"
        ledger.fail_withdrawal(tx)
        return "failed"
    ledger.finish_withdrawal(tx, transfer["id"])
    return "paid"


def settle_withdrawals(sub: str, profile: dict):
    """Finish withdrawals left pending when Stripe didn't answer, each time the person opens their wallet."""
    if not profile.get("payoutAccount"):
        return
    for tx in ledger.pending_withdrawals(sub):
        try:
            send_withdrawal(tx, profile["payoutAccount"])
        except Exception:
            logger.exception("Settling withdrawal %s failed", tx["id"])


def sweep_withdrawals() -> dict:
    """Scheduled: settle everyone's pending withdrawals, including people who haven't come back."""
    outcomes = {"paid": 0, "failed": 0, "pending": 0, "errors": 0}
    for indexed in ledger.all_pending_withdrawals():
        # The index is eventually consistent; act only on what the table says now
        tx = ledger.get_item(indexed["pk"], indexed["sk"])
        if not tx or tx.get("status") != "pending":
            continue
        profile = ledger.get_item(tx["pk"], "PROFILE") or {}
        try:
            outcomes[send_withdrawal(tx, profile["payoutAccount"])] += 1
        except Exception:
            logger.exception("Settling withdrawal %s failed", tx["id"])
            outcomes["errors"] += 1
    return outcomes


@app.get("/api/payouts")
def payouts(user: User = Depends(current_user)):
    """Whether the person can withdraw yet, and in which currency."""
    profile = ledger.ensure_profile(user)
    currency = stripe_call(stripe_client.platform)["currency"]
    account_id = profile.get("payoutAccount")
    if not account_id:
        return {"status": "none", "currency": currency}
    account = stripe_call(stripe_client.get_account, account_id)
    ready = account.get("payouts_enabled") and account.get("capabilities", {}).get("transfers") == "active"
    return {"status": "ready" if ready else "incomplete", "currency": currency}


@app.post("/api/payouts/onboard")
def payouts_onboard(request: Request, user: User = Depends(current_user)):
    """A Stripe link where the person adds their identity and bank details."""
    profile = ledger.ensure_profile(user)
    account_id = profile.get("payoutAccount")
    if not account_id:
        if not profile.get("country"):
            raise ValueError("Choose your country first.")
        account = stripe_call(
            stripe_client.create_connected_account, user_sub=user.sub, email=user.email, country=profile["country"],
        )
        account_id = account["id"]
        ledger.update_profile(user.sub, payoutAccount=account_id)
    return {"url": stripe_call(stripe_client.onboarding_link, account_id, site_origin(request))}


class Withdraw(BaseModel):
    amount: str


@app.post("/api/withdraw")
def withdraw(body: Withdraw, user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    account_id = profile.get("payoutAccount")
    if not account_id:
        raise HTTPException(status_code=409, detail="Set up bank payouts first.")
    account = stripe_call(stripe_client.get_account, account_id)
    if not account.get("payouts_enabled"):
        raise HTTPException(status_code=409, detail="Finish setting up bank payouts first.")
    currency = stripe_call(stripe_client.platform)["currency"]
    amount = parse_amount(body.amount, currency)
    try:
        tx = ledger.start_withdrawal(user.sub, currency, amount)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {currency}. Convert to {currency} first.")
    outcome = send_withdrawal(tx, account_id)
    if outcome == "failed":
        raise HTTPException(status_code=502, detail="Stripe refused the withdrawal, so nothing left your balance.")
    return {"withdrawn": money(amount, currency), "currency": currency, "status": outcome, **statement(user.sub)}


# ── Rewards: referrals and gift cards ────────────────────────────────────────

@app.get("/api/rewards")
def my_rewards(user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    return {
        "referral": rewards.referral_summary(profile, mail.SITE_URL),
        "referredBy": bool(profile.get("referredBy")),
        "giftCards": rewards.gift_cards_bought(user.sub),
        "limits": {"giftMinUsd": str(rewards.GIFT_MIN_USD), "giftMaxUsd": str(rewards.GIFT_MAX_USD)},
    }


class ReferralClaim(BaseModel):
    code: str


@app.post("/api/referral/claim")
def referral_claim(body: ReferralClaim, user: User = Depends(current_user)):
    return {"referrer": rewards.claim_referral(ledger.ensure_profile(user), body.code)}


class GiftCardOrder(BaseModel):
    amount: str
    currency: str
    toEmail: str = ""
    toName: str = ""
    message: str = ""


def money_text(amount: Decimal, currency: str) -> str:
    return f"${money(amount, currency)}" if currency == "USD" else f"{money(amount, currency)} {currency}"


@app.post("/api/giftcards")
def buy_gift_card(body: GiftCardOrder, user: User = Depends(current_user)):
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    details = rewards.clean_gift_card(body.model_dump())
    profile = ledger.ensure_profile(user)
    try:
        card = rewards.buy_gift_card(user.sub, rewards.payer_name(profile) or user.name, currency, amount, details)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {currency}.")
    except KeyError:
        raise ValueError(f"Gift cards can't be made in {currency} yet.")
    emailed = mail.gift_card(card, money_text(amount, currency))
    return {"card": rewards.public_gift_card(card), "emailed": emailed, **statement(user.sub)}


class GiftCardCode(BaseModel):
    code: str


@app.post("/api/giftcards/redeem")
def redeem_gift_card(body: GiftCardCode, user: User = Depends(current_user)):
    ledger.ensure_profile(user)
    card = rewards.redeem_gift_card(user.sub, body.code)
    return {"amount": money(card["amount"], card["currency"]), "currency": card["currency"], "from": card.get("fromName", ""),
            **statement(user.sub)}


@app.post("/api/giftcards/cancel")
def cancel_gift_card(body: GiftCardCode, user: User = Depends(current_user)):
    card = rewards.cancel_gift_card(user.sub, body.code)
    return {"amount": money(card["amount"], card["currency"]), "currency": card["currency"], **statement(user.sub)}


# ── Business accounts ─────────────────────────────────────────────────────────

@app.get("/api/business")
def my_business(user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    return {
        "business": rewards.public_business(profile.get("business")),
        "feeBps": int(rewards.fee_bps(profile, FX_FEE_BPS)), "standardFeeBps": int(FX_FEE_BPS),
        "businessFeeBps": int(min(FX_FEE_BPS, rewards.settings()["businessFeeBps"])), "bulkMax": rewards.BULK_MAX,
    }


@app.post("/api/business/apply")
def apply_business(body: dict, user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    return {"business": rewards.public_business(rewards.apply_business(profile, rewards.clean_business(body)))}


class BulkLine(BaseModel):
    to: str
    amount: str
    note: str = ""


class BulkPay(BaseModel):
    currency: str
    payments: list[BulkLine]


@app.post("/api/business/pay")
def bulk_pay(body: BulkPay, user: User = Depends(current_user)):
    """An approved business pays up to BULK_MAX people in one go; nothing moves unless every line is valid."""
    profile = ledger.ensure_profile(user)
    if not rewards.is_business(profile):
        raise HTTPException(status_code=403, detail="Bulk payments are for approved business accounts.")
    currency = currency_code(body.currency)
    if not body.payments:
        raise ValueError("Add at least one payment.")
    if len(body.payments) > rewards.BULK_MAX:
        raise ValueError(f"Pay at most {rewards.BULK_MAX} people at once.")
    payments, problems = [], []
    for number, line in enumerate(body.payments, 1):
        try:
            found = ledger.find_recipient(handle_of(line.to))
            if not found:
                raise ValueError("No Lyrpay account uses that email or number.")
            if found["sub"] == user.sub:
                raise ValueError("That's you.")
            payments.append((found, parse_amount(line.amount, currency), line.note.strip()[:140]))
        except ValueError as error:
            problems.append(f"Line {number} ({line.to.strip() or 'empty'}): {error}")
    if problems:
        raise ValueError("Nothing was sent. " + " ".join(problems))
    total = sum((amount for _, amount, _ in payments), Decimal(0))
    try:
        ledger.bulk_transfer(user.sub, rewards.payer_name(profile), currency, payments)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You need {money(total, currency)} {currency} for this batch.")
    return {"paid": len(payments), "total": money(total, currency), "currency": currency, **statement(user.sub)}


# ── Admin: promo codes, businesses, rewards settings ─────────────────────────

@app.get("/api/admin/coupons")
def admin_coupons(_: User = Depends(admin_user)):
    return {"coupons": rewards.coupons()}


@app.post("/api/admin/coupons")
def admin_create_coupon(body: dict, admin: User = Depends(admin_user)):
    return {"coupon": rewards.public_coupon(rewards.create_coupon(rewards.clean_coupon(body), admin.email))}


class CouponActive(BaseModel):
    active: bool


@app.patch("/api/admin/coupons/{code}")
def admin_toggle_coupon(code: str, body: CouponActive, _: User = Depends(admin_user)):
    return {"coupon": rewards.public_coupon(rewards.set_coupon_active(code, body.active))}


@app.get("/api/admin/businesses")
def admin_businesses(_: User = Depends(admin_user)):
    found = [p for p in ledger.profiles() if p.get("business")]
    # Applications waiting for a decision first, oldest first
    found.sort(key=lambda p: (p["business"].get("status") != "pending", p["business"].get("applied", "")))
    return {"businesses": [
        {"sub": p["pk"].removeprefix("USER#"), "name": p.get("name", ""), "email": p.get("email", ""), "phone": p.get("phone", ""),
         **rewards.public_business(p["business"])}
        for p in found
    ]}


class BusinessReview(BaseModel):
    status: str
    note: str = ""


@app.post("/api/admin/businesses/{sub}/review")
def admin_review_business(sub: str, body: BusinessReview, admin: User = Depends(admin_user)):
    profile = rewards.review_business(sub, body.status, body.note, admin.email)
    mail.business_decision(profile, profile["business"])
    return {"business": rewards.public_business(profile["business"])}


@app.get("/api/admin/settings")
def admin_settings(_: User = Depends(admin_user)):
    return {"settings": rewards.public_settings(rewards.settings()), "standardFeeBps": int(FX_FEE_BPS)}


@app.put("/api/admin/settings")
def admin_save_settings(body: dict, admin: User = Depends(admin_user)):
    return {"settings": rewards.public_settings(rewards.save_settings(body, admin.email)), "standardFeeBps": int(FX_FEE_BPS)}
