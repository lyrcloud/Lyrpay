import logging
import os
import re
from decimal import Decimal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app import ledger, mail, rates, stripe_client
from app.auth import User, current_user
from app.money import exponent, from_minor, parse_amount, quantize

# Spread on currency conversions, in basis points (50 = 0.5%)
FX_FEE_BPS = Decimal(os.getenv("FX_FEE_BPS", "50"))
ALLOWED_ORIGINS = [o for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o]
CURRENCY = re.compile(r"^[A-Z]{3}$")
PHONE = re.compile(r"^\+[1-9]\d{7,14}$")

logger = logging.getLogger("lyrpay")

app = FastAPI(title="Lyrpay API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["authorization", "content-type"],
)


@app.exception_handler(ValueError)
def value_error(_: Request, error: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(error)})


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
        **statement(user.sub),
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


@app.post("/api/topup")
def topup(body: TopUp, request: Request, user: User = Depends(current_user)):
    currency = currency_code(body.currency)
    amount = parse_amount(body.amount, currency)
    session = stripe_call(
        stripe_client.create_checkout,
        user_sub=user.sub, email=user.email, amount=amount, currency=currency, origin=site_origin(request),
    )
    return {"url": session["url"]}


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
    credited = True
    try:
        ledger.credit_topup(user.sub, body.sessionId, currency, amount)
    except ledger.AlreadyApplied:
        credited = False
    return {"credited": credited, "currency": currency, "amount": money(amount, currency), **statement(user.sub)}


def conversion(source: str, target: str, amount: Decimal) -> tuple[Decimal, Decimal]:
    try:
        mid = rates.rate(source, target)
    except KeyError as error:
        raise ValueError(str(error).strip("'\""))
    applied = mid * (1 - FX_FEE_BPS / 10000)
    return applied, quantize(amount * applied, target)


@app.get("/api/convert/quote")
def convert_quote(source: str, target: str, amount: str):
    source, target = currency_code(source), currency_code(target)
    value = parse_amount(amount, source)
    applied, received = conversion(source, target, value)
    return {"rate": str(applied), "receive": money(received, target), "feeBps": int(FX_FEE_BPS)}


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
    applied, received = conversion(source, target, amount)
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
        ledger.transfer(user, profile.get("name", ""), found, currency, amount, body.note.strip()[:140])
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
