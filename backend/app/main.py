import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app import cards, ledger, lyrbot, mail, paypal_client, plaid_client, rates, rewards, stocks, stripe_app, stripe_client, topup_methods, wise_client
from app.auth import User, current_user
from app.money import exponent, from_minor, parse_amount, quantize

# Spread on currency conversions, in basis points (50 = 0.5%)
FX_FEE_BPS = Decimal(os.getenv("FX_FEE_BPS", "50"))
ALLOWED_ORIGINS = [o for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o]
# Super admins: verified emails that can open the admin overview
MIN_TOPUP_USD = Decimal("0.50")
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


STRIPE_APP_PATH = "/api/stripe-app/"


@app.middleware("http")
async def stripe_app_cors(request: Request, call_next):
    """The Stripe App runs in a sandboxed frame in the Stripe Dashboard (origin "null"), so its endpoints answer any
    origin; they need Stripe's signature instead of a sign-in, which a browser can't add on its own."""
    if not request.url.path.startswith(STRIPE_APP_PATH):
        return await call_next(request)
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "POST, OPTIONS",
               "Access-Control-Allow-Headers": "content-type, stripe-signature", "Access-Control-Max-Age": "600"}
    if request.method == "OPTIONS":
        return HTMLResponse("", status_code=204, headers=headers)
    response = await call_next(request)
    response.headers.update(headers)
    return response


@app.exception_handler(ValueError)
def value_error(_: Request, error: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(cards.CardError)
def card_error(_: Request, error: cards.CardError):
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
    return {"cardPayments": bool(stripe_client.secret_key()), "paypal": paypal_client.env() == "live", "fxFeeBps": int(FX_FEE_BPS)}


def visitor_ip(request: Request) -> str:
    # CloudFront sets CloudFront-Viewer-Address ("ip:port") itself; viewers can't spoof it
    viewer = request.headers.get("cloudfront-viewer-address", "")
    if viewer:
        return viewer.rsplit(":", 1)[0]
    return request.client.host if request.client else "unknown"


@app.post("/api/lyrbot")
async def lyrbot_chat(request: Request):
    """Lyrbot, the site's help assistant: open to every visitor, capped per visitor per hour and site-wide per day."""
    try:
        if not lyrbot.enabled():
            return JSONResponse(status_code=503, content={"error": lyrbot.FALLBACK_REPLY})
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "Invalid JSON body."})
        conversation = lyrbot.clean_messages(body.get("messages") if isinstance(body, dict) else None)
        if not conversation:
            return JSONResponse(status_code=422, content={"error": "Type a question for Lyrbot."})
        limited = lyrbot.limit_message(visitor_ip(request))
        if limited:
            return JSONResponse(status_code=429, content={"error": limited})
        status, answer = await run_in_threadpool(lyrbot.reply, conversation)
        return JSONResponse(status_code=status, content=answer)
    except Exception:
        logger.exception("Lyrbot failed")
        return JSONResponse(status_code=500, content={"error": lyrbot.FALLBACK_REPLY})


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
    settle_topups(user.sub)
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
            "cardsIssued": sum(1 for c in data["cards"] if c.get("status") != "canceled"),
            "cardWaitlist": sum(1 for p in data["profiles"] if p.get("cardWaitlist")),
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
                "heldUsd": round(held_by_user.get(p["pk"], 0.0), 2), "payouts": bool(p.get("payoutAccount") or p.get("bank")),
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


@app.get("/api/stocks/markets")
def stock_markets(group: str = "ai"):
    """Live quotes for AI shares, ETFs, world indices, popular stocks or commodities."""
    return {"group": group, "quotes": stocks.markets(group)}


# ── The Lyrpay Stripe App (stripe-app/) ──────────────────────────────────────

class StripeAppRequest(BaseModel):
    user_id: str
    account_id: str
    payment: str = ""


def stripe_app_caller(request: Request, body: StripeAppRequest):
    if not stripe_app.verified(request.headers.get("stripe-signature", ""), body.user_id, body.account_id):
        raise HTTPException(status_code=401, detail="This request isn't signed by the Lyrpay Stripe App.")
    if body.account_id != stripe_call(stripe_client.platform)["id"]:
        raise HTTPException(status_code=403, detail="The Lyrpay app only works on Lyrpay's own Stripe account.")


@app.post("/api/stripe-app/overview")
def stripe_app_overview(request: Request, body: StripeAppRequest):
    """Lyrpay's totals for the app's Dashboard drawer."""
    stripe_app_caller(request, body)
    data = admin_overview(days=30, _=None)
    return {"updated": data["updated"], "totals": data["totals"], "recent": data["recent"][:8]}


@app.post("/api/stripe-app/payment")
def stripe_app_payment(request: Request, body: StripeAppRequest):
    """For a payment in the Dashboard: whether it's a Lyrpay top-up, whose, and whether it was credited."""
    stripe_app_caller(request, body)
    if not body.payment.startswith("pi_"):
        return {"lyrpay": False}
    session = stripe_call(stripe_client.checkout_for_payment, body.payment)
    metadata = (session or {}).get("metadata") or {}
    if metadata.get("purpose") != "lyrpay_topup" or not metadata.get("user"):
        return {"lyrpay": False}
    sub = metadata["user"]
    profile = ledger.get_item(f"USER#{sub}", "PROFILE") or {}
    credited = bool(ledger.get_item(f"TOPUP#{session['id']}", "TOPUP"))
    waiting = bool(ledger.get_item(f"TOPUP#{session['id']}", "PENDING"))
    return {
        "lyrpay": True,
        "session": session["id"],
        "status": "credited" if credited else "pending" if waiting else "not credited",
        "coupon": metadata.get("coupon", ""),
        "user": {k: profile.get(k, "") for k in ("name", "email", "phone", "country", "currency", "created")},
        **statement(sub),
    }


class TopUp(BaseModel):
    amount: str
    currency: str
    coupon: str = ""
    method: str = "stripe"  # or "paypal"


@app.post("/api/topup")
def topup(body: TopUp, request: Request, user: User = Depends(current_user)):
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    # Stripe won't take a payment under $0.50, or its worth in another currency
    if rewards.usd_value(amount, currency) < MIN_TOPUP_USD:
        least = (MIN_TOPUP_USD * rates.rate("USD", currency)).quantize(Decimal(1).scaleb(-exponent(currency)), rounding=ROUND_UP)
        raise ValueError(f"Add at least {money_text(least, currency)}.")
    coupon = ""
    if body.coupon.strip():
        coupon = rewards.coupon_bonus(body.coupon, user.sub, currency, amount)[0]["code"]
    if body.method == "paypal":
        if not paypal_topups(user.email):
            raise ValueError("PayPal isn't switched on yet. Pay by card instead.")
        return start_paypal(user, amount, currency, site_origin(request), coupon)
    session = start_checkout(user, amount, currency, site_origin(request), coupon)
    # The mobile app confirms the session itself: Checkout returns to the website, where it may not be signed in
    return {"url": session["url"], "sessionId": session["id"]}


def usd_charge(amount: Decimal, currency: str) -> Decimal:
    """What a top-up in a currency Stripe can't charge costs in US dollars, at the mid-market rate, rounded up to a cent."""
    return (amount * rates.rate(currency, "USD")).quantize(Decimal("0.01"), rounding=ROUND_UP)


def start_checkout(user: User, amount: Decimal, currency: str, origin: str, coupon: str) -> dict:
    """A Stripe Checkout session for a top-up. In a currency Stripe can't charge, the person pays the US dollar
    equivalent and the wallet still gets the amount in their currency."""
    common = {"user_sub": user.sub, "email": user.email, "origin": origin, "coupon": coupon}
    if stripe_client.chargeable(currency):
        try:
            return stripe_client.create_checkout(amount=amount, currency=currency, **common)
        except stripe_client.StripeError as error:
            if not (error.definitive and stripe_client.INVALID_CURRENCY.search(str(error))):
                raise HTTPException(status_code=502, detail=str(error))
            stripe_client.UNCHARGEABLE.add(currency)
    return stripe_call(
        stripe_client.create_checkout, amount=usd_charge(amount, currency), currency="USD", credit=(currency, amount), **common,
    )


def paypal_topups(email: str) -> bool:
    """PayPal top-ups once its keys are set. Sandbox keys only for admins, whose test payments credit pretend money:
    nobody else's wallet ever gets a sandbox payment."""
    current = paypal_client.env()
    return current == "live" or (current == "sandbox" and bool(email) and email in ADMIN_EMAILS)


def paypal_call(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except paypal_client.PayPalError as error:
        raise HTTPException(status_code=502, detail=str(error))


PAYPAL_REF = re.compile(r"^paypal_([A-Z0-9]{8,36})$")


def start_paypal(user: User, amount: Decimal, currency: str, origin: str, coupon: str) -> dict:
    """A PayPal order for a top-up. In a currency PayPal can't charge, the person pays the US dollar equivalent and
    the wallet still gets the amount in their currency. The order is recorded so the payment is credited as recorded,
    whatever comes back in the browser."""
    charge_currency, charge = (currency, amount) if paypal_client.payable(currency, amount) else ("USD", usd_charge(amount, currency))
    reference = f"{user.sub}-{int(time.time() * 1000)}"
    description = f"Lyrpay balance top-up: {amount} {currency}"
    order = paypal_call(paypal_client.create_order, reference=reference, amount=charge, currency=charge_currency,
                        description=description, origin=origin)
    ledger.save_paypal_order(order["id"], sub=user.sub, currency=currency, amount=amount, chargeCurrency=charge_currency,
                             charge=paypal_client.value(charge, charge_currency), coupon=coupon, env=paypal_client.env())
    return {"url": order["url"], "sessionId": f"paypal_{order['id']}"}


def paypal_order(ref: str) -> tuple[str, dict]:
    """The order id and Lyrpay's record of a PayPal top-up reference (paypal_<order id>)."""
    match = PAYPAL_REF.match(ref)
    record = ledger.get_item(f"PAYPAL#{match.group(1)}", "ORDER") if match else None
    if not record:
        raise ValueError("That payment link isn't valid.")
    return match.group(1), record


def paypal_capture_matches(capture: dict, record: dict) -> bool:
    paid = capture.get("amount") or {}
    return paid.get("currency_code") == record["chargeCurrency"] and Decimal(paid.get("value", "0")) == Decimal(record["charge"])


def settle_paypal(sub: str, ref: str, order_id: str, record: dict, capture: dict) -> str:
    """Record a PayPal capture: 'paid' (credited), 'pending' (PayPal is holding it) or 'failed'."""
    status = capture.get("status")
    if status in ("DECLINED", "FAILED"):
        waiting = ledger.get_item(f"TOPUP#{ref}", "PENDING")
        if waiting:
            line = ledger.get_item(f"USER#{sub}", waiting["tx"])
            if line:
                ledger.fail_topup(line)
        return "failed"
    if not paypal_capture_matches(capture, record):
        logger.error("PayPal order %s captured %s, expected %s %s: not credited", order_id, capture.get("amount"),
                     record["charge"], record["chargeCurrency"])
        raise HTTPException(status_code=409, detail="That PayPal payment doesn't match the top-up. Contact support.")
    if status == "COMPLETED":
        return "paid"
    if capture.get("id") and not record.get("capture"):
        ledger.note_paypal_capture(order_id, capture["id"])
    ledger.start_topup(sub, ref, record["currency"], Decimal(record["amount"]), counterparty="PayPal")
    return "pending"


def confirm_paypal(user: User, ref: str) -> dict:
    order_id, record = paypal_order(ref)
    if record["sub"] != user.sub:
        raise HTTPException(status_code=409, detail="That payment belongs to another account.")
    if record.get("env") != paypal_client.env():
        raise HTTPException(status_code=409, detail="That PayPal payment was made in test mode.")
    currency, amount = record["currency"], Decimal(record["amount"])
    if ledger.get_item(f"TOPUP#{ref}", "TOPUP"):  # credited already: say so without asking PayPal
        return {"credited": False, "currency": currency, "amount": money(amount, currency), "bonus": money(Decimal(0), currency),
                "couponNote": "", **statement(user.sub)}
    order = paypal_call(paypal_client.get_order, order_id)
    if order.get("status") == "APPROVED":
        try:
            order = paypal_client.capture(order_id)
        except paypal_client.PayPalError as error:
            if error.issue == "ORDER_ALREADY_CAPTURED":
                order = paypal_call(paypal_client.get_order, order_id)
            elif error.definitive:
                # INSTRUMENT_DECLINED and the like: nothing was taken; the person can try again with another way to pay
                raise HTTPException(status_code=409, detail=f"PayPal didn't take the payment: {error}")
            else:
                raise HTTPException(status_code=502, detail=str(error))
    capture = paypal_client.first_capture(order) if order.get("status") == "COMPLETED" else None
    if not capture:
        raise HTTPException(status_code=409, detail="That payment hasn't completed yet.")
    outcome = settle_paypal(user.sub, ref, order_id, record, capture)
    if outcome == "failed":
        raise HTTPException(status_code=409, detail="PayPal declined the payment. Nothing was added.")
    if outcome == "pending":
        return {"credited": False, "pending": True, "currency": currency, "amount": money(amount, currency),
                "bonus": money(Decimal(0), currency), "couponNote": "", **statement(user.sub)}
    return {**credit_payment(user.sub, ref, currency, amount, record.get("coupon", ""), "PayPal"), **statement(user.sub)}


def checkout_credit(session: dict) -> tuple[str, Decimal]:
    """The currency and amount a Checkout session puts in the wallet: what was paid, or for a payment made in US
    dollars for a currency Stripe can't charge, the amount in that currency."""
    meta = session.get("metadata") or {}
    if meta.get("credit_currency") and meta.get("credit_amount"):
        return meta["credit_currency"], Decimal(meta["credit_amount"])
    currency = session["currency"].upper()
    return currency, from_minor(session["amount_total"], currency)


@app.get("/api/coupons/check")
def coupon_check(code: str, amount: str, currency: str, user: User = Depends(current_user)):
    """The bonus a promo code would add to this top-up."""
    currency = currency_code(currency)
    coupon, bonus = rewards.coupon_bonus(code, user.sub, currency, parse_amount(amount, currency))
    return {"code": coupon["code"], "bonus": money(bonus, currency), "currency": currency, "description": coupon.get("description", "")}


@app.get("/api/topup/methods")
def topup_methods_for_me(currency: str = "", user: User = Depends(current_user)):
    """Ways to pay a top-up from the person's country: cards everywhere, plus the local methods Stripe has switched on.
    chargedIn is USD when Stripe can't charge in the chosen currency."""
    ways = topup_methods.for_country(ledger.ensure_profile(user).get("country", ""))
    code = currency.upper()
    paypal = paypal_topups(user.email)
    return {
        **ways, "currency": code, "chargedIn": code if not code or stripe_client.chargeable(code) else "USD",
        # PayPal: whether it's offered, and the currency it charges this top-up in (whole HUF/JPY/TWD amounts aside)
        "paypal": paypal, "paypalTest": paypal and paypal_client.env() == "sandbox",
        "paypalChargedIn": code if not code or code in paypal_client.CURRENCIES else "USD",
    }


class TopUpConfirm(BaseModel):
    sessionId: str


@app.post("/api/topup/confirm")
def topup_confirm(body: TopUpConfirm, user: User = Depends(current_user)):
    if body.sessionId.startswith("paypal_"):
        return confirm_paypal(user, body.sessionId)
    if not re.match(r"^cs_[A-Za-z0-9_]+$", body.sessionId):
        raise ValueError("That payment link isn't valid.")
    session = stripe_call(stripe_client.get_checkout, body.sessionId)
    if session.get("client_reference_id") != user.sub:
        raise HTTPException(status_code=409, detail="That payment belongs to another account.")
    if session.get("payment_status") == "paid":
        return {**credit_checkout(user.sub, body.sessionId, session), **statement(user.sub)}
    # Bank debits (ACH) finish checkout days before the money arrives; the line waits as pending until then
    if session.get("status") == "complete" and session.get("payment_status") == "unpaid":
        currency, amount = checkout_credit(session)
        ledger.start_topup(user.sub, body.sessionId, currency, amount)
        return {
            "credited": False, "pending": True, "currency": currency, "amount": money(amount, currency),
            "bonus": money(Decimal(0), currency), "couponNote": "", **statement(user.sub),
        }
    raise HTTPException(status_code=409, detail="That payment hasn't completed yet.")


def credit_checkout(sub: str, session_id: str, session: dict) -> dict:
    """Credit a paid Checkout session to the person, with its promo bonus and any referral reward, at most once."""
    currency, amount = checkout_credit(session)
    return credit_payment(sub, session_id, currency, amount, (session.get("metadata") or {}).get("coupon", ""), "Stripe")


def credit_payment(sub: str, session_id: str, currency: str, amount: Decimal, code: str, counterparty: str) -> dict:
    """Credit a paid top-up (a Checkout session or a PayPal order) with its promo bonus and any referral reward."""
    # A promo code chosen at checkout is checked again now: it may have run out since
    extras, bonus, coupon_note = [], Decimal(0), ""
    if code:
        try:
            coupon, bonus = rewards.coupon_bonus(code, sub, currency, amount)
            extras = rewards.coupon_writes(coupon, sub, session_id, currency, bonus)
        except ValueError as error:
            coupon_note = str(error)
    credited = True
    try:
        if not ledger.credit_topup(sub, session_id, currency, amount, bonus, extras, counterparty) and extras:
            bonus, coupon_note = Decimal(0), "That promo code ran out before your payment finished."
    except ledger.AlreadyApplied:
        credited, bonus, coupon_note = False, Decimal(0), ""
    # Safe to repeat: the referral bonus is paid at most once per referred person
    rewards.reward_referral(sub, currency, amount)
    return {
        "credited": credited, "currency": currency, "amount": money(amount, currency),
        "bonus": money(bonus, currency), "couponNote": coupon_note,
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


def plaid_payouts(email: str) -> bool:
    """Bank payouts go through Plaid once its keys are set. Sandbox keys only for admins, who test with Plaid's
    pretend banks: nobody else's real balance is ever sent to one."""
    current = plaid_client.env()
    return current == "production" or (current == "sandbox" and bool(email) and email in ADMIN_EMAILS)


def plaid_bank(profile: dict) -> dict | None:
    """The bank account the person linked through Plaid, if it belongs to the Plaid environment in use."""
    bank = profile.get("bank")
    return bank if bank and bank.get("env") == plaid_client.env() else None


def plaid_call(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except plaid_client.PlaidError as error:
        raise HTTPException(status_code=502, detail=str(error))


def send_withdrawal(tx: dict, profile: dict) -> str:
    """Make sure the transfer for a pending withdrawal exists and record the outcome:
    'paid', 'failed' (balance restored) or 'pending' (the answer is still unknown, or the bank hasn't got it yet)."""
    if tx.get("rail") == "plaid":
        return send_plaid_withdrawal(tx, profile)
    if tx.get("rail") == "wise":
        return send_wise_withdrawal(tx)
    account_id = profile.get("payoutAccount")
    if not account_id:
        return "pending"
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


def send_plaid_withdrawal(tx: dict, profile: dict) -> str:
    """Plaid sends the ACH credit; the line stays pending until Plaid posts it to the bank network."""
    bank = plaid_bank(profile)
    if not bank and not tx.get("reference"):
        logger.warning("Withdrawal %s: the Plaid bank account is gone", tx["id"])
        return "pending"
    try:
        if tx.get("reference"):
            transfer = plaid_client.get_transfer(tx["reference"])
        else:
            transfer = plaid_client.pay_out(bank["access"], bank["account"], -tx["amount"], profile.get("name", ""), tx["id"])
            # From now on only its status is asked for, never a second payout
            ledger.note_transfer(tx, transfer["id"])
    except plaid_client.PlaidError as error:
        # A transfer Plaid already has is never written off on an error; only one Plaid refused to make
        if not error.definitive or tx.get("reference"):
            logger.warning("Withdrawal %s: %s", tx["id"], error)
            return "pending"
        logger.warning("Withdrawal %s refused by Plaid: %s", tx["id"], error)
        ledger.fail_withdrawal(tx)
        return "failed"
    if transfer["status"] in ("failed", "cancelled", "returned"):
        ledger.fail_withdrawal(tx)
        return "failed"
    if transfer["status"] in ("posted", "settled", "funds_available"):
        ledger.finish_withdrawal(tx, transfer["id"])
        return "paid"
    return "pending"


def send_wise_withdrawal(tx: dict) -> str:
    """Wise makes the transfer, Lyrpay pays it from its Wise balance, and the line stays pending until Wise has sent it
    to the bank. A transfer Wise refuses, cancels or gets back puts the money back on the balance."""
    try:
        if not tx.get("reference"):
            recipient = int(tx["recipient"])
            offer = wise_client.quote(target=tx["currency"], source_amount=Decimal(str(tx["source"])), recipient=recipient)
            # The same withdrawal always makes the same transfer (customerTransactionId), however often this runs
            transfer = wise_client.create_transfer(recipient=recipient, quote_id=offer["id"], reference=tx["id"])
            ledger.note_transfer(tx, str(transfer["id"]))
        else:
            transfer = wise_client.get_transfer(int(tx["reference"]))
    except wise_client.WiseError as error:
        if not error.definitive or tx.get("reference"):
            logger.warning("Withdrawal %s: %s", tx["id"], error)
            return "pending"
        logger.warning("Withdrawal %s refused by Wise: %s", tx["id"], error)
        ledger.fail_withdrawal(tx)
        return "failed"
    status = transfer.get("status", "")
    if status == "incoming_payment_waiting":
        status = fund_wise(tx, int(tx["reference"]))
    if status in wise_client.FAILED:
        ledger.fail_withdrawal(tx)
        return "failed"
    if status in wise_client.SENT:
        ledger.finish_withdrawal(tx, tx["reference"])
        return "paid"
    return "pending"


def fund_wise(tx: dict, transfer_id: int) -> str:
    """Pay a Wise transfer from Lyrpay's Wise balance; the transfer's status afterwards."""
    try:
        wise_client.fund(transfer_id)
        return "processing"
    except wise_client.WiseError as error:
        if not error.definitive:
            logger.warning("Withdrawal %s: paying Wise transfer %s: %s", tx["id"], transfer_id, error)
            return "incoming_payment_waiting"
        # Refused. Maybe it was paid meanwhile (another request got there first): only an unpaid transfer is cancelled
        logger.warning("Withdrawal %s: Wise wouldn't pay transfer %s from the balance: %s", tx["id"], transfer_id, error)
        try:
            if wise_client.get_transfer(transfer_id).get("status") != "incoming_payment_waiting":
                return wise_client.get_transfer(transfer_id).get("status", "")
            wise_client.cancel(transfer_id)
            return "cancelled"
        except wise_client.WiseError as again:
            logger.warning("Withdrawal %s: %s", tx["id"], again)
            return "incoming_payment_waiting"


def settle_withdrawals(sub: str, profile: dict):
    """Finish withdrawals still pending, each time the person opens their wallet."""
    for tx in ledger.pending(sub, "withdraw"):
        try:
            send_withdrawal(tx, profile)
        except Exception:
            logger.exception("Settling withdrawal %s failed", tx["id"])


def check_topup(tx: dict) -> str:
    """Ask Stripe about a pending bank top-up and record the outcome: 'paid' (credited), 'failed' (the bank didn't
    pay) or 'pending' (still on its way)."""
    sub, session_id = tx["pk"].removeprefix("USER#"), tx["reference"]
    if session_id.startswith("paypal_"):
        return check_paypal_topup(sub, tx)
    try:
        session = stripe_client.get_checkout(session_id)
        if session.get("payment_status") == "paid":
            credit_checkout(sub, session_id, session)
            return "paid"
        intent = session.get("payment_intent")
        if intent and stripe_client.get_payment_intent(intent)["status"] in ("requires_payment_method", "canceled"):
            ledger.fail_topup(tx)
            return "failed"
    except stripe_client.StripeError as error:
        # Refused (a key without PaymentIntents read) or no answer: ask again next time
        logger.warning("Checking top-up %s: %s", session_id, error)
    return "pending"


def check_paypal_topup(sub: str, tx: dict) -> str:
    """A PayPal payment PayPal was holding: credited once it completes, marked failed if it's declined."""
    ref = tx["reference"]
    order_id, record = paypal_order(ref)
    if not record.get("capture") or record.get("env") != paypal_client.env():
        return "pending"
    try:
        capture = paypal_client.get_capture(record["capture"])
    except paypal_client.PayPalError as error:
        logger.warning("Checking PayPal top-up %s: %s", ref, error)
        return "pending"
    outcome = settle_paypal(sub, ref, order_id, record, capture)
    if outcome == "paid":
        credit_payment(sub, ref, record["currency"], Decimal(record["amount"]), record.get("coupon", ""), "PayPal")
    return outcome


def settle_topups(sub: str):
    """Credit bank top-ups that have arrived, each time the person opens their wallet."""
    for tx in ledger.pending(sub, "topup"):
        try:
            check_topup(tx)
        except Exception:
            logger.exception("Checking top-up %s failed", tx["id"])


def sweep(queue: str, settle) -> dict:
    """Scheduled: settle everyone's pending withdrawals or bank top-ups, including people who haven't come back."""
    outcomes = {"paid": 0, "failed": 0, "pending": 0, "errors": 0}
    for indexed in ledger.all_pending(queue):
        # The index is eventually consistent; act only on what the table says now
        tx = ledger.get_item(indexed["pk"], indexed["sk"])
        if not tx or tx.get("status") != "pending":
            continue
        try:
            outcomes[settle(tx)] += 1
        except Exception:
            logger.exception("Settling %s %s failed", queue, tx["id"])
            outcomes["errors"] += 1
    return outcomes


def sweep_withdrawals() -> dict:
    return sweep("withdraw", lambda tx: send_withdrawal(tx, ledger.get_item(tx["pk"], "PROFILE") or {}))


def sweep_topups() -> dict:
    return sweep("topup", check_topup)


def link_bank(sub: str, profile: dict) -> dict | None:
    """Save the bank account picked on the person's Plaid page once they've finished there."""
    link_token = profile.get("bankLink")
    if link_token:
        account = plaid_call(plaid_client.linked_account, link_token)
        if account:
            access = plaid_call(plaid_client.exchange, account["public_token"])
            bank = {"access": access, "account": account["account_id"], "label": account["label"], "env": plaid_client.env()}
            profile = ledger.update_profile(sub, bank=bank, bankLink="")
    return plaid_bank(profile)


# Until the Stripe account is signed up for Connect and its platform profile is complete, Stripe refuses to make payout
# accounts. Each instance remembers the refusal for a while and offers no bank setup meanwhile, then asks again so
# Connect being ready is picked up.
CONNECT_OFF_FOR = 600
_connect_off_until = 0.0
CONNECT_OFF = "Bank withdrawals are coming soon."
CONNECT_NOT_READY = re.compile(r"signed up for Connect|platform profile", re.IGNORECASE)


def connect_off() -> bool:
    return time.monotonic() < _connect_off_until


def wise_payouts(email: str) -> bool:
    """Wise withdrawals once its token is set. Sandbox only for admins, so no real balance is ever sent to a pretend bank."""
    current = wise_client.env()
    return current == "live" or (current == "sandbox" and bool(email) and email in ADMIN_EMAILS)


def wise_call(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except wise_client.WiseError as error:
        raise HTTPException(status_code=400 if error.definitive else 502, detail=str(error))


def wise_recipients(profile: dict) -> dict:
    """The person's bank accounts saved with Wise, by currency, for the Wise environment in use."""
    return {c: r for c, r in (profile.get("wiseRecipients") or {}).items() if r.get("env") == wise_client.env()}


def wise_info(user: User, profile: dict) -> dict | None:
    if not wise_payouts(user.email):
        return None
    return {"test": wise_client.env() == "sandbox", "accounts": {c: r["label"] for c, r in wise_recipients(profile).items()}}


@app.get("/api/payouts")
def payouts(user: User = Depends(current_user)):
    """Whether the person can withdraw yet, and in which currency."""
    profile = ledger.ensure_profile(user)
    try:
        return {**payouts_main(user, profile), "wise": wise_info(user, profile)}
    except HTTPException:
        if not wise_payouts(user.email):
            raise
        # Stripe unreachable or refusing: Wise still works
        return {"status": "unavailable", "currency": "USD", "provider": "stripe", "wise": wise_info(user, profile)}


def payouts_main(user: User, profile: dict) -> dict:
    if plaid_payouts(user.email):
        bank = link_bank(user.sub, profile)
        return {"status": "ready" if bank else "none", "currency": "USD", "provider": "plaid",
                "bank": bank["label"] if bank else "", "test": plaid_client.env() == "sandbox"}
    currency = stripe_call(stripe_client.platform)["currency"]
    account_id = profile.get("payoutAccount")
    if not account_id:
        return {"status": "unavailable" if connect_off() else "none", "currency": currency, "provider": "stripe"}
    account = stripe_call(stripe_client.get_account, account_id)
    ready = account.get("payouts_enabled") and account.get("capabilities", {}).get("transfers") == "active"
    return {"status": "ready" if ready else "incomplete", "currency": currency, "provider": "stripe"}


class Onboard(BaseModel):
    # The mobile app: Plaid ends on its own done screen and the person switches back, instead of landing on the website
    app: bool = False


@app.post("/api/payouts/onboard")
def payouts_onboard(request: Request, body: Onboard | None = None, user: User = Depends(current_user)):
    """A page where the person adds their bank account: Plaid's, or Stripe's (which also checks their identity)."""
    profile = ledger.ensure_profile(user)
    if plaid_payouts(user.email):
        redirect = None if body and body.app else f"{site_origin(request)}/?payouts=done"
        link = plaid_call(plaid_client.hosted_link, user.sub, redirect)
        ledger.update_profile(user.sub, bankLink=link["link_token"])
        return {"url": link["hosted_link_url"]}
    account_id = profile.get("payoutAccount")
    if not account_id:
        if not profile.get("country"):
            raise ValueError("Choose your country first.")
        if connect_off():
            raise HTTPException(status_code=503, detail=CONNECT_OFF)
        try:
            account = stripe_client.create_connected_account(user_sub=user.sub, email=user.email, country=profile["country"])
        except stripe_client.StripeError as error:
            if not (error.definitive and CONNECT_NOT_READY.search(str(error))):
                raise HTTPException(status_code=502, detail=str(error))
            global _connect_off_until
            _connect_off_until = time.monotonic() + CONNECT_OFF_FOR
            # Only the admin needs Stripe's reason
            logger.warning("Stripe Connect isn't ready for this account: %s", error)
            raise HTTPException(status_code=503, detail=CONNECT_OFF)
        account_id = account["id"]
        ledger.update_profile(user.sub, payoutAccount=account_id)
    return {"url": stripe_call(stripe_client.onboarding_link, account_id, site_origin(request))}


class WiseForm(BaseModel):
    currency: str
    details: dict = {}


@app.post("/api/wise/requirements")
def wise_requirements(body: WiseForm, user: User = Depends(current_user)):
    """Wise's bank details form for a currency: the kinds of account it can pay there, each with its fields. Sent
    again with what's filled in when a field changes the rest of the form."""
    if not wise_payouts(user.email):
        raise HTTPException(status_code=503, detail="Wise withdrawals aren't switched on yet.")
    currency = currency_code(body.currency)
    return {"currency": currency, "types": wise_call(wise_client.requirements, currency, Decimal(100), body.details or None)}


class WiseRecipient(BaseModel):
    currency: str
    type: str
    details: dict


def recipient_label(holder: str, kind: str, details: dict) -> str:
    """A name for a saved bank account that doesn't show the full number: the holder and the last digits."""
    numbers = [str(v) for k, v in details.items() if isinstance(v, (str, int)) and any(
        w in k.lower() for w in ("iban", "accountnumber", "account_number", "number", "phone", "email", "clabe", "ifsc"))]
    tail = next((n.replace(" ", "")[-4:] for n in numbers if len(n.replace(" ", "")) >= 4), "")
    return f"{holder} · {kind.replace('_', ' ')}{f' ••{tail}' if tail else ''}"


@app.post("/api/wise/recipient")
def wise_recipient(body: WiseRecipient, user: User = Depends(current_user)):
    """Save the person's bank account with Wise for withdrawals in that currency (replacing one saved before)."""
    if not wise_payouts(user.email):
        raise HTTPException(status_code=503, detail="Wise withdrawals aren't switched on yet.")
    currency = currency_code(body.currency)
    flat = {k: v for k, v in body.details.items() if isinstance(v, (str, int, float, bool))}
    holder = str(flat.pop("accountHolderName", "") or "").strip()
    if not holder:
        raise ValueError("Enter the account holder's full name.")
    if not re.match(r"^[a-z0-9_]{2,40}$", body.type):
        raise ValueError("Choose the kind of bank account.")
    flat.setdefault("legalType", "PRIVATE")
    nested = wise_client.nest(flat)
    account = wise_call(wise_client.create_recipient, currency=currency, kind=body.type, holder=holder, details=nested)
    saved = {**(ledger.ensure_profile(user).get("wiseRecipients") or {}),
             currency: {"id": account["id"], "label": recipient_label(holder, body.type, flat), "env": wise_client.env()}}
    profile = ledger.update_profile(user.sub, wiseRecipients=saved)
    return {"wise": wise_info(user, profile)}


def wise_source_amount(amount: Decimal, currency: str) -> Decimal:
    """What a withdrawal of `amount` in `currency` takes from Lyrpay's Wise balance, at the mid-market rate."""
    try:
        return quantize(amount * rates.rate(currency, wise_client.source()), wise_client.source())
    except KeyError:
        raise ValueError(f"Withdrawals in {currency} aren't available.")


@app.get("/api/wise/quote")
def wise_quote(currency: str, amount: str, user: User = Depends(current_user)):
    """What arrives in the bank for a Wise withdrawal, after Wise's fee."""
    if not wise_payouts(user.email):
        raise HTTPException(status_code=503, detail="Wise withdrawals aren't switched on yet.")
    currency = currency_code(currency)
    value = parse_amount(amount, currency)
    recipient = wise_recipients(ledger.ensure_profile(user)).get(currency)
    offer = wise_call(wise_client.quote, target=currency, source_amount=wise_source_amount(value, currency),
                      recipient=int(recipient["id"]) if recipient else None)
    fee = quantize(offer["fee"] * rates.rate(wise_client.source(), currency), currency)
    return {"currency": currency, "amount": money(value, currency), "receive": money(offer["receive"], currency),
            "fee": money(fee, currency), "notice": offer["warning"]}


class Withdraw(BaseModel):
    amount: str
    currency: str = ""
    rail: str = ""  # "wise" for a withdrawal through Wise, in `currency`


@app.post("/api/withdraw")
def withdraw(body: Withdraw, user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    if body.rail == "wise":
        return withdraw_wise(body, user, profile)
    if plaid_payouts(user.email):
        bank = plaid_bank(profile)
        if not bank:
            raise HTTPException(status_code=409, detail="Add your bank account first.")
        currency, details = "USD", {"rail": "plaid", "counterparty": bank["label"]}
    else:
        account_id = profile.get("payoutAccount")
        if not account_id:
            raise HTTPException(status_code=409, detail="Set up bank payouts first.")
        account = stripe_call(stripe_client.get_account, account_id)
        if not account.get("payouts_enabled"):
            raise HTTPException(status_code=409, detail="Finish setting up bank payouts first.")
        currency, details = stripe_call(stripe_client.platform)["currency"], {}
    amount = parse_amount(body.amount, currency)
    try:
        tx = ledger.start_withdrawal(user.sub, currency, amount, **details)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {currency}. Convert to {currency} first.")
    outcome = send_withdrawal(tx, profile)
    if outcome == "failed":
        raise HTTPException(status_code=502, detail="The bank transfer was refused, so nothing left your balance.")
    return {"withdrawn": money(amount, currency), "currency": currency, "status": outcome, **statement(user.sub)}


def withdraw_wise(body: Withdraw, user: User, profile: dict) -> dict:
    if not wise_payouts(user.email):
        raise HTTPException(status_code=503, detail="Wise withdrawals aren't switched on yet.")
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    recipient = wise_recipients(profile).get(currency)
    if not recipient:
        raise HTTPException(status_code=409, detail=f"Add a bank account for {currency} first.")
    source_amount = wise_source_amount(amount, currency)
    try:
        tx = ledger.start_withdrawal(user.sub, currency, amount, rail="wise", counterparty=recipient["label"],
                                     recipient=int(recipient["id"]), source=source_amount)
    except ledger.InsufficientFunds:
        raise HTTPException(status_code=409, detail=f"You don't have enough {currency}.")
    outcome = send_wise_withdrawal(tx)
    if outcome == "failed":
        raise HTTPException(status_code=502, detail="Wise refused the transfer, so nothing left your balance.")
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



# ── Cards (Stripe Issuing) ───────────────────────────────────────────────────

@app.get("/api/cards")
def my_cards(user: User = Depends(current_user)):
    profile = ledger.ensure_profile(user)
    live = cards.issuing_live()
    platform = stripe_call(stripe_client.platform) if live else {}
    return {
        "live": live, "country": platform.get("country", ""), "currency": platform.get("currency", ""),
        "eligible": live and profile.get("country") == platform.get("country"),
        "cards": cards.cards_of(user.sub), "waitlisted": bool(profile.get("cardWaitlist")),
        "publishableKey": cards.PUBLISHABLE_KEY, "termsUrl": cards.CARD_TERMS_URL,
    }


@app.post("/api/cards/waitlist")
def card_waitlist(user: User = Depends(current_user)):
    ledger.ensure_profile(user)
    ledger.update_profile(user.sub, cardWaitlist=ledger._now())
    return {"waitlisted": True}


@app.post("/api/cards")
def new_card(body: dict, request: Request, user: User = Depends(current_user)):
    if not cards.issuing_live():
        raise HTTPException(status_code=409, detail="Lyrpay cards aren't available yet. Join the waitlist and we'll email you.")
    details = cards.clean_application(body)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "")
    card = stripe_call(cards.issue_card, ledger.ensure_profile(user), details, ip, request.headers.get("user-agent", ""))
    return {"card": card}


class CardFreeze(BaseModel):
    frozen: bool


@app.post("/api/cards/{card_id}/freeze")
def freeze_card(card_id: str, body: CardFreeze, user: User = Depends(current_user)):
    return {"card": stripe_call(cards.set_frozen, user.sub, card_id, body.frozen)}


class CardReveal(BaseModel):
    nonce: str


@app.post("/api/cards/{card_id}/reveal")
def reveal_card(card_id: str, body: CardReveal, user: User = Depends(current_user)):
    """An ephemeral key so Stripe.js can show this card's details; only for the card's owner."""
    cards.card_of(user.sub, card_id)
    key = stripe_call(stripe_client.card_ephemeral_key, card_id, body.nonce)
    return {"secret": key["secret"], "version": stripe_client.EPHEMERAL_KEY_VERSION}


@app.post("/api/stripe/issuing")
async def issuing_webhook(request: Request):
    """Stripe Issuing events. Authorization requests are answered in the response, within Stripe's 2 seconds."""
    payload = await request.body()
    secret = cards.webhook_secret()
    if not secret or not cards.verify(payload, request.headers.get("stripe-signature", ""), secret):
        raise HTTPException(status_code=400, detail="Bad signature.")
    event = json.loads(payload)
    answer = cards.handle_event(event)
    if answer is None:
        return {"received": True}
    return JSONResponse(answer, headers={"Stripe-Version": event.get("api_version") or ""})
