from decimal import ROUND_DOWN, Decimal, InvalidOperation

# ISO 4217 minor units that aren't the usual 2. Stripe follows the same list,
# except it wants three-decimal amounts sent with a trailing zero.
ZERO_DECIMAL = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "MGA", "PYG",
    "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}
THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}

MAX_AMOUNT = Decimal("1000000")


def exponent(currency: str) -> int:
    currency = currency.upper()
    if currency in ZERO_DECIMAL:
        return 0
    if currency in THREE_DECIMAL:
        return 3
    return 2


def quantize(amount: Decimal, currency: str) -> Decimal:
    """Round down to the currency's smallest unit, so a conversion never credits more than it took."""
    return amount.quantize(Decimal(1).scaleb(-exponent(currency)), rounding=ROUND_DOWN)


def parse_amount(raw, currency: str) -> Decimal:
    """A positive amount in the currency's major unit, or ValueError with a message fit for the person."""
    try:
        amount = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("Enter an amount.")
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Enter an amount greater than zero.")
    if amount > MAX_AMOUNT:
        raise ValueError("That amount is above the limit for a single payment.")
    if quantize(amount, currency) != amount:
        raise ValueError(f"{currency.upper()} takes at most {exponent(currency)} decimal places.")
    return amount


def to_minor(amount: Decimal, currency: str) -> int:
    return int(amount.scaleb(exponent(currency)))


def from_minor(minor: int, currency: str) -> Decimal:
    return Decimal(minor).scaleb(-exponent(currency))
