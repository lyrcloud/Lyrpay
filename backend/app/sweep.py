import logging

from app.main import sweep_topups, sweep_withdrawals
from app.merchants import sweep_callbacks

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    """Runs every 15 minutes (template.yaml SweepFunction)."""
    outcomes = {"withdrawals": sweep_withdrawals(), "topups": sweep_topups(), "callbacks": sweep_callbacks()}
    logging.info("Sweep: %s", outcomes)
    return outcomes
