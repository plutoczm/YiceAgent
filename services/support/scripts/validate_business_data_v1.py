from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a SupportOps Business Data Contract v1 delivery directory."
    )
    parser.add_argument("--delivery", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    from app.business_data.models import DeliveryValidationError
    from app.business_data.validate_v1 import validate_business_data_delivery_v1

    args = parse_args()
    try:
        result = validate_business_data_delivery_v1(args.delivery)
    except DeliveryValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
