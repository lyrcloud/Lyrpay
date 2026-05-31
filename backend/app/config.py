import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    BINANCE_API_URL: str = os.getenv("BINANCE_API_URL", "https://api.binance.com")
    COINBASE_API_URL: str = os.getenv("COINBASE_API_URL", "https://api.coinbase.com")
    STRIPE_API_KEY: str = os.getenv("STRIPE_API_KEY", "")
    PLAID_CLIENT_ID: str = os.getenv("PLAID_CLIENT_ID", "")
    PLAID_SECRET: str = os.getenv("PLAID_SECRET", "")

settings = Settings()
