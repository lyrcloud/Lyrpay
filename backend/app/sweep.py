import logging

from app.main import sweep_withdrawals

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    """Runs every 15 minutes (template.yaml SweepFunction)."""
    outcomes = sweep_withdrawals()
    logging.info("Withdrawal sweep: %s", outcomes)
    return outcomes
