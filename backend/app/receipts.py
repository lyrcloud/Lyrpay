"""Receipt and tracking emails for every statement line, driven by the ledger table's stream.

A new TX# line sends a receipt; a line whose status later changes (a withdrawal paid out or returned, a bank top-up
arriving or failing) sends a tracking update. Each email links to the line's details in the wallet (lyrpay.com/?tx=id).
Phone-only accounts have no email, and people who unsubscribed get none.
"""
import logging
from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer

from app import ledger, mail
from app.money import exponent

logger = logging.getLogger("lyrpay")
_deserialize = TypeDeserializer().deserialize


def _image(record: dict, key: str) -> dict | None:
    raw = (record.get("dynamodb") or {}).get(key)
    return {k: _deserialize(v) for k, v in raw.items()} if raw else None


def money_text(amount, currency: str) -> str:
    value = abs(Decimal(str(amount))).quantize(Decimal(1).scaleb(-exponent(currency)))
    return f"${value:,}" if currency == "USD" else f"{value:,} {currency}"


def describe(tx: dict, update: bool) -> tuple[str, str]:
    """(subject, first line) for a statement line; update: its status just changed."""
    amount = money_text(tx["amount"], tx["currency"])
    who = tx.get("counterparty") or ""
    status = tx.get("status") or ""
    kind = tx["kind"]
    if kind == "withdraw":
        if status == "failed":
            return f"Your {amount} withdrawal came back", f"Your {amount} withdrawal couldn't be paid out, so it's back on your Lyrpay balance."
        if status == "paid":
            return f"{amount} is on its way to your bank", f"Your {amount} withdrawal has been sent to {who or 'your bank'}."
        return f"Withdrawal of {amount} requested", f"We're processing your {amount} withdrawal to {who or 'your bank'}. We'll email you when it's sent."
    if kind == "topup":
        if status == "pending":
            return f"{amount} is on its way", f"Your {amount} top-up is on its way from your bank. We'll email you when it arrives."
        if status == "failed":
            return "Your top-up didn't go through", f"Your bank didn't pay the {amount} top-up, so nothing was added."
        return f"{amount} added to your balance", f"{amount} was added to your Lyrpay balance{f' through {who}' if who else ''}."
    if kind == "convert" and tx.get("toAmount"):
        received = money_text(tx["toAmount"], tx.get("toCurrency", ""))
        return f"You converted {amount} to {received}", f"You converted {amount} into {received}."
    lines = {
        "send": (f"You sent {amount} to {who}", f"You sent {amount} to {who}."),
        "receive": (f"{who or 'Someone'} sent you {amount}", f"{who or 'Someone'} sent you {amount}. It's in your Lyrpay balance now."),
        "payment": (f"You paid {amount} to {who}", f"You paid {who or 'a Lyr app'} {amount} from your Lyrpay balance."),
        "giftcard": (f"Your {amount} gift card", f"You bought a Lyrpay gift card for {amount}."),
        "redeem": (f"Gift card redeemed: {amount}", f"You redeemed a gift card. {amount} is in your balance."),
        "refund": (f"Refund of {amount}", f"{amount} was refunded to your Lyrpay balance."),
        "bonus": (f"Bonus: {amount}", f"A bonus of {amount} was added to your balance."),
        "card": (f"Card payment of {amount}", f"Your Lyrpay card paid {amount}{f' at {who}' if who else ''}."),
        "adjust": ("Your balance was adjusted", f"Lyrpay {'added' if Decimal(str(tx['amount'])) > 0 else 'took'} {amount} "
                                                 f"{'to' if Decimal(str(tx['amount'])) > 0 else 'from'} your balance."),
    }
    subject, line = lines.get(kind, (f"Lyrpay: {amount}", f"A {amount} {kind} on your Lyrpay account."))
    if update and status == "failed":
        return f"Update: {subject}", f"{line} It didn't go through, so nothing was taken."
    return subject, line


def send_receipt(tx: dict, update: bool = False) -> bool:
    sub = tx["pk"].removeprefix("USER#")
    profile = ledger.get_item(f"USER#{sub}", "PROFILE") or {}
    to = mail.mailable(profile.get("email"))
    if not to or ledger.opted_out(to):
        return False
    subject, line = describe(tx, update)
    details = [f"Amount: {'-' if str(tx['amount']).startswith('-') else '+'}{money_text(tx['amount'], tx['currency'])}"]
    if tx.get("counterparty"):
        details.append(f"{'To' if str(tx['amount']).startswith('-') else 'From'}: {tx['counterparty']}")
    if tx.get("note"):
        details.append(f"Note: {tx['note']}")
    status = {"pending": "Processing", "failed": "Failed, nothing was taken", "paid": "Completed"}.get(tx.get("status") or "", "Completed")
    number = ledger.tracking_number(tx["id"])
    if tx.get("status") == "pending" and tx.get("waiting") == "funds":
        status = "Processing: waiting for payout funds to clear, usually within 2 business days"
    details += [f"Status: {status}", f"Date: {str(tx['at'])[:19].replace('T', ' ')} UTC", f"Tracking number: {number}"]
    live = tx.get("status") == "pending"
    text, html = mail.render(
        subject, [f"Hi {mail.first_name(profile.get('name'))},", line, "\n".join(details)],
        button=("Track it live" if live else "See details", f"{mail.SITE_URL}/?track={number}"),
        footer=mail._footer("You're getting this because money moved on your Lyrpay account.", to),
    )
    mail.send(to, subject, text, html, mail.unsubscribe_url(to))
    return True


def handler(event, context):
    """DynamoDB stream: INSERT and MODIFY of TX# lines (the event source filters to those)."""
    sent = 0
    for record in event.get("Records", []):
        try:
            new, old = _image(record, "NewImage"), _image(record, "OldImage")
            if not new or not str(new.get("sk", "")).startswith("TX#"):
                continue
            if record.get("eventName") == "INSERT":
                sent += send_receipt(new)
            elif record.get("eventName") == "MODIFY" and old and (old.get("status") or "") != (new.get("status") or ""):
                sent += send_receipt(new, update=True)
        except Exception:
            # One bad email never holds up the rest of the stream
            logger.exception("Receipt for %s failed", ((record.get("dynamodb") or {}).get("Keys") or {}))
    return {"sent": sent}
