"""One Lyrpay wallet per person, whichever Lyr sign-in they use.

People reach Lyrpay with a Lyr account (the Lyrtrip pool: Lyrpay, Lyrtrip) or a Lyrcloud account (the Lyrcloud pool:
Lyrcloud, Lyrsoft). Each pool gives its own `sub`, so the same person can arrive with two. Every sign-in identity is
mapped to one canonical wallet id, kept as

    IDENTITY#<pool>#<sub>   IDENTITY   wallet, email, phone, linkedBy, at

The first time an identity is seen, it joins the wallet that already owns its VERIFIED email or phone (the HANDLE#
items payments use); otherwise it becomes its own wallet. Unverified details never link, so nobody can reach someone
else's wallet by signing up with their address. Names and dates of birth are never used to link (many people share
them): they only flag possible duplicates for the super admin to review (possible_duplicates).
"""
import logging
import re
import unicodedata

from app import ledger

logger = logging.getLogger("lyrpay")

# Pools Lyrpay accepts, by issuer: a short name for each (the Lyr pool keeps its subs as wallet ids, as before)
LYR = "lyr"
LYRCLOUD = "lyrcloud"

_cache: dict[tuple[str, str], str] = {}


def _key(pool: str, sub: str) -> dict:
    return {"pk": f"IDENTITY#{pool}#{sub}", "sk": "IDENTITY"}


def wallet_for(pool: str, sub: str, email: str, phone: str) -> str:
    """The wallet id for this sign-in identity, linking it on first sight by verified email or phone."""
    cached = _cache.get((pool, sub))
    if cached:
        return cached
    item = ledger.table().get_item(Key=_key(pool, sub)).get("Item")
    if item:
        _cache[(pool, sub)] = item["wallet"]
        return item["wallet"]
    wallet, linked_by = sub, "new"
    # A sign-in that already has its own wallet keeps it: re-pointing it would hide that wallet's money
    has_own = ledger.table().get_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}).get("Item") is not None
    for handle, how in (() if has_own else ((email, "email"), (phone, "phone"))):
        if not handle:
            continue
        owner = ledger.table().get_item(Key={"pk": f"HANDLE#{handle}", "sk": "HANDLE"}).get("Item")
        if owner and owner.get("sub"):
            wallet, linked_by = owner["sub"], how
            break
    try:
        ledger.table().put_item(
            Item={**_key(pool, sub), "wallet": wallet, "pool": pool, "email": email, "phone": phone,
                  "linkedBy": linked_by, "at": ledger._now()},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ledger.ClientError as error:
        if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Another request recorded it first: use what it decided
        wallet = ledger.table().get_item(Key=_key(pool, sub), ConsistentRead=True)["Item"]["wallet"]
    if linked_by != "new":
        logger.info("Identity %s/%s joined wallet %s by verified %s", pool, sub, wallet, linked_by)
    _cache[(pool, sub)] = wallet
    return wallet


def identities(wallet: str) -> list[dict]:
    """Every sign-in identity that reaches this wallet (for the admin)."""
    from boto3.dynamodb.conditions import Attr
    return [{k: i.get(k, "") for k in ("pool", "email", "phone", "linkedBy", "at")} | {"sub": i["pk"].split("#", 2)[2]}
            for i in ledger._scan(FilterExpression=Attr("sk").eq("IDENTITY") & Attr("wallet").eq(wallet))]


def _norm_name(name: str) -> str:
    plain = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(sorted(re.findall(r"[a-z]+", plain)))


def possible_duplicates() -> list[dict]:
    """Groups of different wallets that look like the same person, for review (never merged automatically):
    the same name with the same date of birth, or the same name in the same country with the same phone digits
    once formatting is stripped."""
    groups: dict[tuple, list[dict]] = {}
    for profile in ledger.profiles():
        name = _norm_name(profile.get("name"))
        if len(name) < 3:
            continue
        sub = profile["pk"].removeprefix("USER#")
        if sub.startswith("merchant_"):
            continue
        brief = {"sub": sub, "name": profile.get("name", ""), "email": profile.get("email", ""),
                 "phone": profile.get("phone", ""), "country": profile.get("country", ""), "dob": profile.get("dob", "")}
        if profile.get("dob"):
            groups.setdefault(("name+dob", name, str(profile["dob"])), []).append(brief)
        digits = re.sub(r"\D", "", str(profile.get("phone") or ""))[-9:]
        if digits:
            groups.setdefault(("name+phone", name, digits), []).append(brief)
    return [{"reason": reason, "accounts": accounts} for (reason, *_), accounts in groups.items() if len(accounts) > 1]
