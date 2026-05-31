from pydantic import BaseModel
from typing import Literal

class AuthRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    expires_in: int


class TradeQuoteRequest(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    amount: float
    currency: str = "USD"


class TradeOrderRequest(TradeQuoteRequest):
    slippage_tolerance: float = 0.005


class TradeResponse(BaseModel):
    symbol: str
    side: str
    amount: float
    price: float
    estimated_total: float
    status: str
    details: dict


class WalletBalanceResponse(BaseModel):
    user_id: str
    balances: dict


class MarketTickerResponse(BaseModel):
    symbol: str
    price: float
    timestamp: str
