import logging

from app.main import sweep_topups, sweep_withdrawals

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    """Runs every 15 minutes (template.yaml SweepFunction)."""
    outcomes = {"withdrawals": sweep_withdrawals(), "topups": sweep_topups()}
    logging.info("Sweep: %s", outcomes)
    return outcomes
