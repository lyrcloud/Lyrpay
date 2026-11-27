import logging

from app import btc_rewards
from app.main import sweep_topups, sweep_withdrawals
from app.merchants import sweep_callbacks

logging.getLogger().setLevel(logging.INFO)


def handler(event, context):
    """Runs every 15 minutes (template.yaml SweepFunction)."""
    outcomes = {"withdrawals": sweep_withdrawals(), "topups": sweep_topups(), "callbacks": sweep_callbacks(),
                "bitcoinRewards": btc_rewards.sweep()}
    logging.info("Sweep: %s", outcomes)
    return outcomes
