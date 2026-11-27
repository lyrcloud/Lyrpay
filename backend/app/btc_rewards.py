"""Points redeemed as Bitcoin, sent from Lyrpay's own Coinbase account to the person's Coinbase email.

Lyrpay never holds Bitcoin for anyone: it keeps a small BTC float in its Coinbase account, and a redemption buys nothing
and sends from that float at the market price (100 points = $1 of BTC). The person needs a Coinbase account for that
email; sends between Coinbase accounts stay off the blockchain, so there is no network fee.

A redemption takes the points and writes the reward (status "sending") and a queue entry in one transaction, then asks
Coinbase to send. If Coinbase refuses, the points go back. If its answer is lost, the queue entry stays, and the sweep
asks again with the same idem, which Coinbase answers with the first send rather than sending twice.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal

from boto3.dynamodb.conditions import Key

from app import coinbase_client, rates
from app.ledger import TABLE_NAME, _now, _put, _transact, table
from app.rewards import EMAIL, POINTS_PER_USD, Conflict

MIN_POINTS = 500  # $5, so a reward is worth Coinbase's minimums and the person's trouble
SATOSHI = Decimal("0.00000001")
QUEUE = "QUEUE#btcreward"
OPEN = ("sending", "pending")

logger = logging.getLogger("lyrpay")


def available(is_admin: bool = False) -> bool:
    return coinbase_client.ready() and (is_admin or not coinbase_client.admins_only())


def btc_price_usd() -> Decimal:
    for coin in rates.crypto_markets("usd"):
        if coin["symbol"] == "BTC" and coin.get("price"):
            return Decimal(str(coin["price"]))
    raise ValueError("The Bitcoin price isn't available right now. Try again in a minute.")


def public(item: dict) -> dict:
    return {"id": item["id"], "at": item["at"], "points": int(item["points"]), "usd": str(item["usd"]),
            "btc": f"{Decimal(item['btc']):.8f}", "to": item["to"], "status": item["status"],
            "reason": item.get("reason", "")}


def history(sub: str, limit: int = 10) -> list[dict]:
    items = table().query(KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with("BTCREWARD#"),
                          ScanIndexForward=False, Limit=limit)["Items"]
    return [public(i) for i in items]


def redeem(sub: str, points, email: str) -> dict:
    """Take the points and send their worth in BTC to the Coinbase account with this email."""
    try:
        points = int(points)
    except (TypeError, ValueError):
        raise ValueError("Enter a whole number of points.")
    if points < MIN_POINTS:
        raise ValueError(f"Redeem at least {MIN_POINTS:,} points as Bitcoin.")
    email = (email or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 254:
        raise ValueError("Enter the email address of your Coinbase account.")
    usd = (Decimal(points) / POINTS_PER_USD).quantize(Decimal("0.01"))
    price = btc_price_usd()
    btc = (usd / price).quantize(SATOSHI, rounding=ROUND_DOWN)
    at, reward_id = _now(), uuid.uuid4().hex[:16]
    item = {"pk": f"USER#{sub}", "sk": f"BTCREWARD#{at}#{reward_id}", "id": reward_id, "at": at, "sub": sub,
            "points": points, "usd": usd, "btc": btc, "priceUsd": price, "to": email,
            "idem": str(uuid.uuid4()), "status": "sending"}
    _transact([
        {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": f"USER#{sub}", "sk": "PROFILE"},
            "UpdateExpression": "SET pointsBalance = pointsBalance - :p, pointsRedeemed = if_not_exists(pointsRedeemed, :zero) + :p",
            "ConditionExpression": "pointsBalance >= :p",
            "ExpressionAttributeValues": {":p": points, ":zero": 0},
        }},
        _put(item),
        _put({"pk": QUEUE, "sk": f"{at}#{sub}#{reward_id}", "rewardPk": item["pk"], "rewardSk": item["sk"]}),
    ], Conflict("You don't have that many points."))
    return public(settle(item))


def _close(item: dict, status: str, refund: bool, **fields) -> dict:
    """Finish a reward once: mark it, drop it from the queue, and on failure give the points back."""
    names = {"#s": "status"}
    values = {":s": status, ":open1": OPEN[0], ":open2": OPEN[1], **{f":{k}": v for k, v in fields.items()}}
    sets = ", ".join(["#s = :s"] + [f"{k} = :{k}" for k in fields])
    ops = [
        {"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": item["pk"], "sk": item["sk"]},
            "UpdateExpression": f"SET {sets}", "ConditionExpression": "#s IN (:open1, :open2)",
            "ExpressionAttributeNames": names, "ExpressionAttributeValues": values,
        }},
        {"Delete": {"TableName": TABLE_NAME, "Key": {"pk": QUEUE, "sk": _queue_sk(item)}}},
    ]
    if refund:
        ops.append({"Update": {
            "TableName": TABLE_NAME, "Key": {"pk": item["pk"], "sk": "PROFILE"},
            "UpdateExpression": "SET pointsBalance = pointsBalance + :p, pointsRedeemed = pointsRedeemed - :p",
            "ExpressionAttributeValues": {":p": int(item["points"])},
        }})
    try:
        _transact(ops, Conflict("already settled"))
    except Conflict:
        pass  # another run settled it first
    return {**item, "status": status, **fields}


def _queue_sk(item: dict) -> str:
    return f"{item['at']}#{item['sub']}#{item['id']}"


def settle(item: dict) -> dict:
    """Ask Coinbase to send (again, with the same idem) and record what it says."""
    try:
        sent = coinbase_client.send_btc(item["to"], f"{Decimal(item['btc']):.8f}", item["idem"], "Your Lyrpay rewards")
    except coinbase_client.CoinbaseError as error:
        if error.definitive:
            logger.warning("Bitcoin reward %s refused by Coinbase: %s", item["id"], error)
            return _close(item, "failed", refund=True, reason=str(error)[:200])
        logger.warning("Bitcoin reward %s: no answer from Coinbase yet (%s)", item["id"], error)
        return item
    if sent["status"] in coinbase_client.FAILED:
        return _close(item, "failed", refund=True, reason=f"Coinbase {sent['status']} the send.", txId=sent["id"])
    if sent["status"] in coinbase_client.SENT:
        return _close(item, "sent", refund=False, txId=sent["id"])
    if item.get("status") != "pending" or item.get("txId") != sent["id"]:
        table().update_item(Key={"pk": item["pk"], "sk": item["sk"]}, UpdateExpression="SET #s = :s, txId = :t",
                            ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "pending", ":t": sent["id"]})
    return {**item, "status": "pending", "txId": sent["id"]}


def sweep(older_than_seconds: int = 60) -> dict:
    """Settle every reward still open (run by the 15-minute sweep)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)).isoformat(timespec="milliseconds")
    queued = table().query(KeyConditionExpression=Key("pk").eq(QUEUE) & Key("sk").lte(cutoff))["Items"]
    outcomes: dict[str, int] = {}
    for entry in queued:
        item = table().get_item(Key={"pk": entry["rewardPk"], "sk": entry["rewardSk"]}, ConsistentRead=True).get("Item")
        if not item or item["status"] not in OPEN:
            table().delete_item(Key={"pk": QUEUE, "sk": entry["sk"]})
            continue
        status = settle(item)["status"]
        outcomes[status] = outcomes.get(status, 0) + 1
    return outcomes
