from fastapi import APIRouter, Depends, HTTPException
from app.schemas import (
    AuthRequest,
    AuthResponse,
    TradeQuoteRequest,
    TradeOrderRequest,
    TradeResponse,
    WalletBalanceResponse,
    MarketTickerResponse,
)
from app.services.crypto_service import CryptoService
from app.services.payment_gateway import PaymentGateway

router = APIRouter()
crypto_service = CryptoService()
payment_gateway = PaymentGateway()


def fake_auth(token: str = ""):  # placeholder for real auth
    if token != "demo-token":
        raise HTTPException(status_code=401, detail="Invalid auth token")
    return {"user_id": "demo-user", "role": "trader"}


@router.post("/auth/login", response_model=AuthResponse)
def login(data: AuthRequest):
    return AuthResponse(token="demo-token", expires_in=3600)


@router.get("/wallet/balance", response_model=WalletBalanceResponse)
def wallet_balance(user=Depends(fake_auth)):
    return crypto_service.get_wallet_balance(user["user_id"])


@router.get("/market/ticker", response_model=MarketTickerResponse)
def market_ticker(symbol: str = "BTCUSDT"):
    return crypto_service.fetch_market_ticker(symbol)


@router.post("/trade/quote", response_model=TradeResponse)
def trade_quote(request: TradeQuoteRequest, user=Depends(fake_auth)):
    return crypto_service.build_trade_quote(request)


@router.post("/trade/order", response_model=TradeResponse)
def trade_order(request: TradeOrderRequest, user=Depends(fake_auth)):
    return crypto_service.execute_trade(request)


@router.post("/fiat/deposit")
def deposit_fiat(amount: float, currency: str = "USD", user=Depends(fake_auth)):
    result = payment_gateway.create_payment_intent(amount, currency)
    return {"status": "pending", "payment_intent": result}


@router.post("/fiat/withdraw")
def withdraw_fiat(amount: float, currency: str = "USD", user=Depends(fake_auth)):
    result = payment_gateway.create_withdrawal_request(amount, currency)
    return {"status": "requested", "withdrawal": result}
