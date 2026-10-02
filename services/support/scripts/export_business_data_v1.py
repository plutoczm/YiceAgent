from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def parse_generated_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("generated_at_must_be_rfc3339") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("generated_at_must_include_timezone")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write a deterministic SupportOps Business Data Contract v1 delivery."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument(
        "--generated-at",
        type=parse_generated_at,
        help="Optional RFC 3339 timestamp for reproducible deliveries.",
    )
    return parser.parse_args()


def main() -> int:
    from app.business_data.export_v1 import write_business_data_export_v1
    from app.business_data.models import DeliveryContext, ExportValidationError
    from app.config import Settings
    from app.store import SupportStore

    args = parse_args()
    try:
        context = DeliveryContext(tenant_id=args.tenant_id, currency=args.currency)
        settings = Settings.from_env()
        store = SupportStore(settings.database_url)
        result = write_business_data_export_v1(
            store.engine,
            context,
            args.output,
            generated_at=args.generated_at,
        )
    except (ExportValidationError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    datasets = result.manifest["datasets"]
    assert isinstance(datasets, list)
    summary = {
        "contract_version": result.manifest["contract_version"],
        "currency": result.manifest["currency"],
        "dataset_row_counts": {
            dataset["dataset_name"]: dataset["row_count"] for dataset in datasets
        },
        "output_path": str(result.output_path),
        "tenant_id": result.manifest["tenant_id"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
