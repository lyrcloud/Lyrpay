import os
import time
from datetime import datetime
from typing import Dict
import httpx
from app.schemas import TradeOrderRequest, TradeQuoteRequest, TradeResponse, WalletBalanceResponse, MarketTickerResponse

class CryptoService:
    def __init__(self):
        self.binance_api = os.getenv("BINANCE_API_URL", "https://api.binance.com")
        self.coinbase_api = os.getenv("COINBASE_API_URL", "https://api.coinbase.com")
        self.http_client = httpx.AsyncClient() if False else httpx.Client()

    def get_wallet_balance(self, user_id: str) -> WalletBalanceResponse:
        return WalletBalanceResponse(
            user_id=user_id,
            balances={
                "USD": 12000.50,
                "BTC": 0.845,
                "ETH": 14.3,
            },
        )

    def fetch_market_ticker(self, symbol: str = "BTCUSDT") -> MarketTickerResponse:
        price = self._fetch_binance_price(symbol)
        return MarketTickerResponse(
            symbol=symbol,
            price=price,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

    def build_trade_quote(self, request: TradeQuoteRequest) -> TradeResponse:
        market_price = self._fetch_binance_price(request.symbol)
        estimated_total = market_price * request.amount
        return TradeResponse(
            symbol=request.symbol,
            side=request.side,
            amount=request.amount,
            price=market_price,
            estimated_total=estimated_total,
            status="quote_ready",
            details={"exchange": "binance", "timestamp": datetime.utcnow().isoformat()},
        )

    def execute_trade(self, request: TradeOrderRequest) -> TradeResponse:
        executed_price = self._fetch_binance_price(request.symbol) * (1 + (0.001 if request.side == "buy" else -0.001))
        total = executed_price * request.amount
        return TradeResponse(
            symbol=request.symbol,
            side=request.side,
            amount=request.amount,
            price=executed_price,
            estimated_total=total,
            status="filled",
            details={
                "exchange": "binance",
                "slippage": request.slippage_tolerance,
                "executed_at": datetime.utcnow().isoformat(),
            },
        )

    def _fetch_binance_price(self, symbol: str) -> float:
        try:
            response = self.http_client.get(f"{self.binance_api}/api/v3/ticker/price", params={"symbol": symbol})
            response.raise_for_status()
            return float(response.json().get("price", 0.0))
        except Exception:
            return 45000.0 if symbol.startswith("BTC") else 3200.0
