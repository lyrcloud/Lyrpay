import logging

from mangum import Mangum

from app import cards
from app.main import app

logger = logging.getLogger("lyrpay")

_asgi = Mangum(app)


def handler(event, context):
    # A schedule pings the function every few minutes (template.yaml), so card authorizations, which Stripe gives
    # 2 seconds, rarely meet a cold start
    # The ping also checks whether Stripe Issuing is on (at most every 10 min), so the logs say why cards are off
    if event.get("warm"):
        try:
            cards_live = cards.issuing_live()
        except Exception:
            logger.exception("Card availability check failed")
            cards_live = None
        return {"warm": True, "cardsLive": cards_live}
    return _asgi(event, context)
