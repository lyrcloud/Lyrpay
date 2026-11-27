import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import btc_rewards, coinbase_client, rates
from tests.test_api import CAROL, as_user, aws, top_up  # noqa: F401  (fixtures)
from tests.test_points import convert, points


class FakeCoinbase:
    def __init__(self):
        self.sends = []
        self.answer = {"id": "cb_tx_1", "status": "completed", "network": "off_blockchain"}
        self.error = None

    def send_btc(self, to, amount, idem, description=""):
        self.sends.append({"to": to, "amount": amount, "idem": idem})
        if self.error:
            raise self.error
        return self.answer


def setup(monkeypatch, as_user, earn_usd="12000"):
    fake = FakeCoinbase()
    monkeypatch.setattr(coinbase_client, "ready", lambda: True)
    monkeypatch.setattr(coinbase_client, "admins_only", lambda: False)
    monkeypatch.setattr(coinbase_client, "send_btc", fake.send_btc)
    monkeypatch.setattr(rates, "crypto_markets", lambda vs="usd": [{"symbol": "BTC", "price": 60000.0}])
    carol = as_user(CAROL)
    top_up(carol, monkeypatch, "carol", "cs_btc_1", amount_total=1300000)
    convert(as_user(CAROL), earn_usd)  # $60 of fees: 600 points
    return fake


def test_bitcoin_rewards_hidden_until_coinbase_is_set_up(as_user, monkeypatch):
    monkeypatch.setattr(coinbase_client, "ready", lambda: False)
    carol = as_user(CAROL)
    assert carol.get("/api/rewards").json()["bitcoin"]["available"] is False
    assert carol.post("/api/points/bitcoin", json={"points": 500, "email": "c@example.com"}).status_code == 409


def test_points_become_bitcoin_sent_to_coinbase(as_user, monkeypatch):
    fake = setup(monkeypatch, as_user)
    carol = as_user(CAROL)
    assert points(carol)["balance"] == 600
    assert carol.post("/api/points/bitcoin", json={"points": 400, "email": "c@example.com"}).status_code == 400
    assert carol.post("/api/points/bitcoin", json={"points": 500, "email": "not-an-email"}).status_code == 400
    assert carol.post("/api/points/bitcoin", json={"points": 700, "email": "c@example.com"}).status_code == 409

    reward = carol.post("/api/points/bitcoin", json={"points": 500, "email": "Carol@Example.com"}).json()["reward"]
    # $5 at $60,000 a bitcoin, rounded down to the satoshi
    assert (reward["btc"], reward["usd"], reward["status"], reward["to"]) == ("0.00008333", "5.00", "sent", "carol@example.com")
    assert fake.sends == [{"to": "carol@example.com", "amount": "0.00008333", "idem": fake.sends[0]["idem"]}]
    rewards = as_user(CAROL).get("/api/rewards").json()
    assert rewards["points"]["balance"] == 100
    assert rewards["bitcoin"]["email"] == "carol@example.com"
    assert [r["status"] for r in rewards["bitcoin"]["history"]] == ["sent"]
    assert btc_rewards.sweep(older_than_seconds=0) == {}  # nothing left in the queue


def test_refused_send_gives_the_points_back(as_user, monkeypatch):
    fake = setup(monkeypatch, as_user)
    fake.error = coinbase_client.CoinbaseError("Not enough funds", definitive=True)
    carol = as_user(CAROL)
    response = carol.post("/api/points/bitcoin", json={"points": 500, "email": "c@example.com"})
    assert response.status_code == 502 and "points are back" in response.json()["detail"]
    rewards = as_user(CAROL).get("/api/rewards").json()
    assert rewards["points"]["balance"] == 600
    assert rewards["bitcoin"]["history"][0]["status"] == "failed"
    assert btc_rewards.sweep(older_than_seconds=0) == {}


def test_lost_answer_is_asked_again_with_the_same_idem(as_user, monkeypatch):
    fake = setup(monkeypatch, as_user)
    fake.error = coinbase_client.CoinbaseError("timeout", definitive=False)
    carol = as_user(CAROL)
    assert carol.post("/api/points/bitcoin", json={"points": 500, "email": "c@example.com"}).json()["reward"]["status"] == "sending"
    assert points(as_user(CAROL))["balance"] == 100

    fake.error = None
    fake.answer = {"id": "cb_tx_2", "status": "pending", "network": "off_blockchain"}
    assert btc_rewards.sweep(older_than_seconds=0) == {"pending": 1}
    fake.answer = {"id": "cb_tx_2", "status": "completed", "network": "off_blockchain"}
    assert btc_rewards.sweep(older_than_seconds=0) == {"sent": 1}
    assert btc_rewards.sweep(older_than_seconds=0) == {}
    assert len({s["idem"] for s in fake.sends}) == 1 and len(fake.sends) == 3
    assert as_user(CAROL).get("/api/rewards").json()["bitcoin"]["history"][0]["status"] == "sent"


def test_requests_are_signed_with_a_short_lived_es256_jwt(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption()).decode()
    name = "organizations/org1/apiKeys/key1"
    monkeypatch.setattr(coinbase_client, "keys", lambda: {"key_name": name, "private_key": pem})
    token = coinbase_client._token("POST", "/v2/accounts/abc/transactions")
    header = jwt.get_unverified_header(token)
    claims = jwt.decode(token, key.public_key(), algorithms=["ES256"])
    assert header["kid"] == name and header["nonce"]
    assert claims["sub"] == name and claims["iss"] == "cdp" and claims["exp"] - claims["nbf"] == 120
    assert claims["uri"] == "POST api.coinbase.com/v2/accounts/abc/transactions"
