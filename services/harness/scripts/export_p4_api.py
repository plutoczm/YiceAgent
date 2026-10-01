"""Export the implemented P4 request/result schema without creating services."""

import json
import sys
from pathlib import Path

from app.adapters.analytics_dto import ExportView, ReportView
from app.workflows.models import AnalyticsReport, RefreshSnapshot, RunRequest, RunResponse


def main() -> None:
    """Write the closed P4 service and workflow wire contracts."""
    contract = {
        "version": "v1",
        "phase": "P4",
        "workflows": {
            "refresh_snapshot": RefreshSnapshot.model_json_schema(),
            "analytics_report": AnalyticsReport.model_json_schema(),
        },
        "run_request": RunRequest.model_json_schema(),
        "run_response": RunResponse.model_json_schema(),
        "services": {
            "POST /internal/demo/exports": {
                "request": RefreshSnapshot.model_json_schema(),
                "response": ExportView.model_json_schema(),
            },
            "POST /internal/demo/reports": {
                "request": AnalyticsReport.model_json_schema(),
                "response": ReportView.model_json_schema(),
            },
        },
    }
    Path(sys.argv[1]).write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
