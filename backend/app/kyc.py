"""Know your customer: two levels a person can reach, kept on the profile as `kyc`.

Level 1, phone: Twilio Lookup asks the carrier what kind of line the number is (only real mobile lines count, not VoIP
or virtual numbers, which is what banks and UPI/M-Pesa style apps tie money to), then Twilio Verify texts a code.
Works for numbers in any country. Uses the Twilio account the Lyr apps share (secret lyrshop/twilio).

Level 2, identity: a photo ID and a matching selfie through Stripe Identity (documents from 120+ countries). Needs the
Stripe key's Identity permissions; until then it reports itself unavailable.

An admin can override the outcome (verified or rejected) from the account editor.

  kyc = {phone: {number, lineType, carrier, at}, document: {session, status, error, at},
         override: "verified" | "rejected" | "", note, reviewedBy, reviewedAt}
"""
import json
import logging
import os
import time

import boto3
import httpx

from app import ledger, stripe_client

logger = logging.getLogger("lyrpay")

TWILIO_SECRET_ID = os.getenv("TWILIO_SECRET_ID", "lyrshop/twilio")
VERIFY_SERVICE_SID = os.getenv("TWILIO_VERIFY_SERVICE_SID", "")
# Lines a person keeps with their bank or wallet; VoIP, landline, toll-free and pager numbers don't count
MOBILE_LINES = {"mobile", "personal", "fixedVoip"}
REFUSED_LINES = {"nonFixedVoip", "voip", "landline", "tollFree", "premium", "sharedCost", "uan", "voicemail", "pager"}

_twilio: dict | None = None
_twilio_read = 0.0


class KycError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def twilio() -> dict | None:
    """account_sid and auth_token (and maybe verify_service_sid), or None when Twilio isn't reachable."""
    global _twilio, _twilio_read
    if _twilio is None and time.monotonic() - _twilio_read > 300:
        _twilio_read = time.monotonic()
        try:
            _twilio = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=TWILIO_SECRET_ID)["SecretString"])
        except Exception:
            logger.warning("Twilio secret %s isn't readable; phone verification is off", TWILIO_SECRET_ID)
    return _twilio if _twilio and _twilio.get("account_sid") and _twilio.get("auth_token") else None


def _twilio_call(method: str, url: str, data: dict | None = None) -> dict:
    creds = twilio()
    if not creds:
        raise KycError("Phone verification isn't switched on yet.", 503)
    try:
        response = httpx.request(method, url, data=data, auth=(creds["account_sid"], creds["auth_token"]), timeout=15)
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise KycError("The phone check didn't answer. Try again in a minute.", 502)
    if response.status_code >= 400 and response.status_code != 404:
        logger.warning("Twilio %s %s: %s %s", method, url, response.status_code, body.get("message"))
        raise KycError(body.get("message") or "The phone check failed.", 502 if response.status_code >= 500 else 400)
    return {**body, "_status": response.status_code}


def verify_service() -> str:
    """Lyrpay's Twilio Verify service: from the environment or the secret, else made once and remembered."""
    if VERIFY_SERVICE_SID:
        return VERIFY_SERVICE_SID
    saved = ledger.get_item("SETTINGS", "TWILIO") or {}
    if saved.get("verifyService"):
        return saved["verifyService"]
    made = _twilio_call("POST", "https://verify.twilio.com/v2/Services", {"FriendlyName": "Lyrpay", "CodeLength": "6"})
    ledger.table().put_item(Item={"pk": "SETTINGS", "sk": "TWILIO", "verifyService": made["sid"], "at": ledger._now()})
    return made["sid"]


def line_type(phone: str) -> dict:
    """What the carrier says the number is: {type, carrier}."""
    found = _twilio_call("GET", f"https://lookups.twilio.com/v2/PhoneNumbers/{phone}?Fields=line_type_intelligence")
    if found["_status"] == 404 or found.get("valid") is False:
        raise KycError("That number doesn't exist. Check it and the country code.")
    info = found.get("line_type_intelligence") or {}
    return {"type": info.get("type") or "", "carrier": info.get("carrier_name") or ""}


def start_phone(sub: str, phone: str) -> dict:
    line = line_type(phone)
    if line["type"] in REFUSED_LINES:
        raise KycError("Use the mobile number your bank or wallet knows you by. Internet, landline and virtual numbers "
                       "can't be verified.")
    owner = ledger.find_recipient(phone)
    if owner and owner["sub"] != sub:
        raise KycError("That number is already verified on another Lyrpay account.", 409)
    _twilio_call("POST", f"https://verify.twilio.com/v2/Services/{verify_service()}/Verifications", {"To": phone, "Channel": "sms"})
    ledger.table().put_item(Item={"pk": f"USER#{sub}", "sk": "KYCPHONE", "number": phone, "lineType": line["type"],
                                  "carrier": line["carrier"], "at": ledger._now()})
    return {"sent": True, "lineType": line["type"]}


def check_phone(sub: str, code: str) -> dict:
    waiting = ledger.get_item(f"USER#{sub}", "KYCPHONE")
    if not waiting:
        raise KycError("Ask for a code first.")
    if not code.isdigit() or not 4 <= len(code) <= 10:
        raise KycError("Enter the code from the text.")
    result = _twilio_call("POST", f"https://verify.twilio.com/v2/Services/{verify_service()}/VerificationCheck",
                          {"To": waiting["number"], "Code": code})
    if result.get("status") != "approved":
        raise KycError("That code isn't right, or it expired. Ask for a new one.")
    phone = waiting["number"]
    owner = ledger.find_recipient(phone)
    if owner and owner["sub"] != sub:
        raise KycError("That number is already verified on another Lyrpay account.", 409)
    profile = ledger.get_item(f"USER#{sub}", "PROFILE") or {}
    kyc = dict(profile.get("kyc") or {})
    kyc["phone"] = {"number": phone, "lineType": waiting.get("lineType", ""), "carrier": waiting.get("carrier", ""), "at": ledger._now()}
    ledger.table().delete_item(Key={"pk": f"USER#{sub}", "sk": "KYCPHONE"})
    # The verified number also finds this person for payments
    ledger.table().put_item(Item={"pk": f"HANDLE#{phone}", "sk": "HANDLE", "sub": sub})
    ledger.update_profile(sub, kyc=kyc, **({} if profile.get("phone") else {"phone": phone}))
    return kyc


def identity_available() -> bool:
    return bool(stripe_client.secret_key())


def start_document(sub: str, return_url: str) -> str:
    """A Stripe Identity page where the person photographs their ID and takes a selfie; returns its URL."""
    try:
        session = stripe_client.identity_session(sub, return_url)
    except stripe_client.StripeError as error:
        if "permission" in str(error).lower() or "not set up" in str(error).lower() or "activate" in str(error).lower():
            logger.warning("Stripe Identity isn't available: %s", error)
            raise KycError("ID verification isn't switched on yet.", 503)
        raise KycError(str(error), 400 if error.definitive else 502)
    profile = ledger.get_item(f"USER#{sub}", "PROFILE") or {}
    kyc = dict(profile.get("kyc") or {})
    kyc["document"] = {"session": session["id"], "status": session.get("status", "requires_input"), "error": "", "at": ledger._now()}
    ledger.update_profile(sub, kyc=kyc)
    return session["url"]


def refresh_document(sub: str, profile: dict) -> dict:
    """Ask Stripe how an unfinished ID check went, and keep the answer."""
    kyc = dict(profile.get("kyc") or {})
    document = dict(kyc.get("document") or {})
    if not document.get("session") or document.get("status") in ("verified", "canceled"):
        return kyc
    try:
        session = stripe_client.get_identity_session(document["session"])
    except stripe_client.StripeError as error:
        logger.warning("Identity session %s: %s", document["session"], error)
        return kyc
    status = session.get("status", "")
    error = (session.get("last_error") or {}).get("reason") or ""
    if status != document.get("status") or error != document.get("error"):
        document.update(status=status, error=error, checked=ledger._now())
        kyc["document"] = document
        ledger.update_profile(sub, kyc=kyc)
    return kyc


def level(kyc: dict | None) -> int:
    kyc = kyc or {}
    if kyc.get("override") == "rejected":
        return 0
    if kyc.get("override") == "verified" or (kyc.get("document") or {}).get("status") == "verified":
        return 2
    return 1 if kyc.get("phone") else 0


def public(kyc: dict | None) -> dict:
    """What the person (and the admin list) sees."""
    kyc = kyc or {}
    document = kyc.get("document") or {}
    lvl = level(kyc)
    if kyc.get("override") == "rejected":
        status = "rejected"
    elif lvl == 2:
        status = "verified"
    elif document.get("status") == "processing":
        status = "pending"
    else:
        status = "phone" if lvl == 1 else "unverified"
    phone = kyc.get("phone") or {}
    return {
        "status": status, "level": lvl,
        "phone": {"number": phone.get("number", ""), "lineType": phone.get("lineType", ""), "at": phone.get("at", "")} if phone else None,
        "document": {"status": document.get("status", ""), "error": document.get("error", ""), "at": document.get("at", "")} if document else None,
        "override": kyc.get("override", ""), "note": kyc.get("note", ""),
    }
