import time
from concurrent.futures import ThreadPoolExecutor

import httpx

# Live share prices from Yahoo Finance's public chart endpoint (no key needed).
# Prices during market hours are ~15 min delayed at worst; kept for a minute per warm Lambda.
CHART_URLS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{}",
)
TTL_SECONDS = 60

GROUPS: dict[str, list[tuple[str, str]]] = {
    "ai": [
        ("NVDA", "NVIDIA"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"), ("META", "Meta"), ("AMZN", "Amazon"),
        ("AMD", "AMD"), ("AVGO", "Broadcom"), ("TSM", "TSMC"), ("PLTR", "Palantir"), ("ARM", "Arm Holdings"),
        ("ORCL", "Oracle"), ("SMCI", "Super Micro"), ("CRM", "Salesforce"), ("SNOW", "Snowflake"),
        ("MU", "Micron"), ("ASML", "ASML"),
    ],
    "etf": [
        ("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq 100 ETF"), ("VOO", "Vanguard S&P 500"), ("VTI", "Total US Market"),
        ("SMH", "Semiconductor ETF"), ("BOTZ", "Robotics & AI ETF"), ("AIQ", "AI & Technology ETF"),
        ("ARKK", "ARK Innovation"), ("VGT", "Vanguard Info Tech"), ("IWM", "Russell 2000 ETF"),
        ("DIA", "Dow Jones ETF"), ("GLD", "Gold ETF"), ("TLT", "20+ Yr Treasury"), ("VWO", "Emerging Markets"),
    ],
    "index": [
        ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000"),
        ("^VIX", "Volatility (VIX)"), ("^FTSE", "FTSE 100"), ("^GDAXI", "DAX"), ("^FCHI", "CAC 40"),
        ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"), ("^NSEI", "Nifty 50"), ("^BSESN", "Sensex"),
        ("^AXJO", "ASX 200"), ("^KS11", "KOSPI"),
    ],
    "stock": [
        ("AAPL", "Apple"), ("TSLA", "Tesla"), ("NFLX", "Netflix"), ("BRK-B", "Berkshire Hathaway"),
        ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("MA", "Mastercard"), ("WMT", "Walmart"), ("LLY", "Eli Lilly"),
        ("XOM", "Exxon Mobil"), ("KO", "Coca-Cola"), ("DIS", "Disney"), ("NKE", "Nike"), ("COIN", "Coinbase"),
        ("UBER", "Uber"), ("SHOP", "Shopify"),
    ],
    "commodity": [
        ("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "Crude Oil (WTI)"), ("BZ=F", "Brent Crude"),
        ("NG=F", "Natural Gas"), ("HG=F", "Copper"), ("PL=F", "Platinum"), ("ZC=F", "Corn"),
    ],
}

_cache: dict[str, tuple[float, list]] = {}
_http = httpx.Client(timeout=8, headers={"User-Agent": "Mozilla/5.0 (compatible; Lyrpay markets)"})
_pool = ThreadPoolExecutor(max_workers=16)


def _quote(symbol: str, name: str) -> dict | None:
    for url in CHART_URLS:
        try:
            body = _http.get(url.format(symbol), params={"range": "5d", "interval": "1h"}).raise_for_status().json()
            result = body["chart"]["result"][0]
            break
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            continue
    else:
        return None
    meta = result["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    previous = meta.get("previousClose") or meta.get("chartPreviousClose")
    change = meta.get("regularMarketChangePercent")
    if change is None and previous:
        change = (price / previous - 1) * 100
    closes = [c for c in ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [] if c is not None]
    return {
        "symbol": symbol, "name": name, "price": price, "change": change, "currency": meta.get("currency") or "USD",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "high52": meta.get("fiftyTwoWeekHigh"), "low52": meta.get("fiftyTwoWeekLow"),
        "dayHigh": meta.get("regularMarketDayHigh"), "dayLow": meta.get("regularMarketDayLow"),
        "volume": meta.get("regularMarketVolume"), "time": meta.get("regularMarketTime"),
        "sparkline": closes[-40:],
    }


def markets(group: str) -> list:
    """Quotes for one group (ai, etf, index, stock, commodity), skipping any symbol Yahoo didn't answer."""
    if group not in GROUPS:
        raise ValueError(f"Unknown market group {group}.")
    hit = _cache.get(group)
    if hit and time.time() - hit[0] < TTL_SECONDS:
        return hit[1]
    quotes = [q for q in _pool.map(lambda s: _quote(*s), GROUPS[group]) if q]
    if not quotes:
        if hit:
            return hit[1]
        raise ValueError("Share prices are unavailable right now.")
    _cache[group] = (time.time(), quotes)
    return quotes
