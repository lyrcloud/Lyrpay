import time
from decimal import Decimal

import httpx

# Mid-market rates for every currency open.er-api.com covers (~165), refreshed
# at most every 10 minutes per warm Lambda. The source updates once a day.
FX_URL = "https://open.er-api.com/v6/latest/USD"
CRYPTO_URL = "https://api.coingecko.com/api/v3/coins/markets"
TTL_SECONDS = 600

_cache: dict[str, tuple[float, object]] = {}
_http = httpx.Client(timeout=10)


def _cached(key: str, load):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL_SECONDS:
        return hit[1]
    try:
        value = load()
    except (httpx.HTTPError, ValueError, KeyError):
        if hit:
            return hit[1]
        raise
    _cache[key] = (time.time(), value)
    return value


def _load_fx() -> dict:
    body = _http.get(FX_URL).raise_for_status().json()
    if body.get("result") != "success":
        raise ValueError("FX source unavailable")
    return {"updated": body["time_last_update_utc"], "rates": body["rates"]}


def usd_rates() -> dict:
    """{'updated': str, 'rates': {currency: units per 1 USD}}"""
    return _cached("fx", _load_fx)


def rate(source: str, target: str) -> Decimal:
    rates = usd_rates()["rates"]
    if source not in rates or target not in rates:
        raise KeyError(f"{source if source not in rates else target} isn't supported yet.")
    return Decimal(str(rates[target])) / Decimal(str(rates[source]))


def crypto_markets(vs_currency: str = "usd") -> list:
    def load():
        response = _http.get(CRYPTO_URL, params={
            "vs_currency": vs_currency.lower(), "order": "market_cap_desc", "per_page": 50, "page": 1,
            "price_change_percentage": "24h",
        })
        return [
            {
                "id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"], "image": c["image"],
                "price": c["current_price"], "change24h": c.get("price_change_percentage_24h"),
                "marketCap": c.get("market_cap"),
            }
            for c in response.raise_for_status().json()
        ]

    return _cached(f"crypto:{vs_currency.lower()}", load)
