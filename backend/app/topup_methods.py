"""Which ways to pay a top-up work for someone, by their country: the same rule for every country.

Checkout already offers every method switched on in the Stripe Dashboard that fits the payer (stripe_client.py,
create_checkout), so this only tells people beforehand what they'll find there. Methods come from Stripe's
country and currency table for a US Stripe account (docs.stripe.com/payments/payment-methods/payment-method-support)
and are shown only once the Dashboard has them switched on.
"""
import logging
import time

from . import stripe_client

EU = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
    "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
SEPA = EU | {"IS", "LI", "NO", "CH", "GB", "MC", "SM", "AD", "VA", "GI"}
EVERYWHERE = None

# Stripe payment method type: (label, customer countries or EVERYWHERE, the one currency it takes or None for any)
LOCAL = {
    "us_bank_account": ("US bank account (ACH)", {"US"}, "USD"),
    "cashapp": ("Cash App Pay", {"US"}, "USD"),
    "affirm": ("Affirm", {"US"}, "USD"),
    "afterpay_clearpay": ("Afterpay", {"US"}, "USD"),
    "klarna": ("Klarna", {"US"}, "USD"),
    "amazon_pay": ("Amazon Pay", EVERYWHERE, "USD"),
    "crypto": ("Stablecoins (USDC)", EVERYWHERE, "USD"),
    "sepa_debit": ("SEPA bank debit", SEPA, "EUR"),
    "ideal": ("iDEAL | Wero", {"NL"}, "EUR"),
    "bancontact": ("Bancontact", {"BE"}, "EUR"),
    "eps": ("EPS", {"AT"}, "EUR"),
    "p24": ("Przelewy24", {"PL"}, None),  # EUR or PLN
    "mb_way": ("MB WAY", {"PT"}, "EUR"),
    "multibanco": ("Multibanco", {"PT"}, "EUR"),
    "pay_by_bank": ("Pay by Bank", {"GB", "IE", "FI"}, None),  # GBP or EUR
    "upi": ("UPI", {"IN"}, "INR"),
    "pix": ("Pix", {"BR"}, "BRL"),
    "alipay": ("Alipay", {"CN", "HK"}, None),
    "wechat_pay": ("WeChat Pay", {"CN", "HK"}, None),
}
CARDS = ["Visa, Mastercard and other cards", "Apple Pay", "Google Pay", "Link"]

logger = logging.getLogger("lyrpay")

KEEP_FOR = 600
_enabled: set | None = None
_enabled_read = 0.0


def enabled() -> set | None:
    """Payment method types switched on in the Stripe Dashboard (the default configuration), read every 10 minutes.
    None when Stripe won't say, e.g. a restricted key without Payment Method Configurations read."""
    global _enabled, _enabled_read
    if time.monotonic() - _enabled_read > KEEP_FOR:
        _enabled_read = time.monotonic()
        try:
            configs = stripe_client.payment_method_configurations()
            default = next((c for c in configs if c.get("is_default")), configs[0] if configs else None)
            if default is not None:
                _enabled = {
                    kind for kind in LOCAL
                    if (default.get(kind) or {}).get("available")
                    and ((default.get(kind) or {}).get("display_preference") or {}).get("value") == "on"
                }
                logger.info("Top-up methods switched on in Stripe: %s", sorted(_enabled))
        except stripe_client.StripeError as error:
            # Usually the restricted key lacks "Payment Method Configurations: Read". Keep what was read before
            logger.warning("Stripe wouldn't list payment method settings: %s", error)
    return _enabled


def for_country(country: str) -> dict:
    """What someone in `country` (ISO code, may be empty) can pay a top-up with: cards always, plus local methods."""
    country = (country or "").upper()
    known = enabled()
    on = known or set()
    local = [
        {"type": kind, "label": label, "currency": currency or ""}
        for kind, (label, countries, currency) in LOCAL.items()
        if kind in on and (countries is EVERYWHERE or country in countries)
    ]
    # Local methods for this country first, the ones open to everyone after
    local.sort(key=lambda m: LOCAL[m["type"]][1] is EVERYWHERE)
    return {
        "country": country,
        "cards": CARDS,
        "local": local,
        # Stripe has nothing local switched on for this country: internationally enabled cards are the way to pay.
        # Not claimed when Stripe wouldn't say what's switched on
        "cardsOnly": known is not None and bool(country) and not any(LOCAL[m["type"]][1] is not EVERYWHERE for m in local),
    }
