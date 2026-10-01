"""Export the implemented P3 request/result schemas using the Harness environment."""

import json
from pathlib import Path

from app.workflows.models import REQUESTS, RunRecord, RunRequest, RunResponse

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    """Publish static machine-readable schemas without starting services."""
    path = ROOT / "contracts/service_api/v1/p3.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "version": "workflowops-service-api-v1-p3",
        "workflows": {name: model.model_json_schema() for name, model in REQUESTS.items()},
        "run_request": RunRequest.model_json_schema(),
        "run_response": RunResponse.model_json_schema(),
        "run_record": RunRecord.model_json_schema(),
        "http_timeout_seconds": 30,
        "run_timeout_seconds": 60,
        "mode": "offline",
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
