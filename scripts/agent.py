"""Explicit owned agent schema preparation, preserving all business and historical data."""

import argparse

from workflowops.agent.store import prepare
from workflowops.config import Settings
from workflowops.database import make_engine


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["prepare"])
    p.add_argument("--approved", action="store_true")
    args = p.parse_args()
    if not args.approved:
        raise SystemExit("explicit_approval_required")
    engine = make_engine(Settings.load())
    try:
        prepare(engine)
        print("Owned context/memory schema ready; existing business data retained.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
