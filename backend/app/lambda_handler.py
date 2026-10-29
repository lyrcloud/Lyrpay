from mangum import Mangum
from app.main import app

_asgi = Mangum(app)


def handler(event, context):
    # A schedule pings the function every few minutes (template.yaml), so card authorizations, which Stripe gives
    # 2 seconds, rarely meet a cold start
    if event.get("warm"):
        return {"warm": True}
    return _asgi(event, context)
