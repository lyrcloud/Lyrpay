import os
from datetime import datetime

class PaymentGateway:
    def __init__(self):
        self.stripe_api_key = os.getenv("STRIPE_API_KEY", "")
        self.plaid_client_id = os.getenv("PLAID_CLIENT_ID", "")

    def create_payment_intent(self, amount: float, currency: str) -> dict:
        return {
            "amount": amount,
            "currency": currency,
            "payment_provider": "stripe",
            "client_secret": "pi_demo_secret",
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

    def create_withdrawal_request(self, amount: float, currency: str) -> dict:
        return {
            "amount": amount,
            "currency": currency,
            "provider": "bank_rail",
            "status": "pending",
            "requested_at": datetime.utcnow().isoformat() + "Z",
        }
