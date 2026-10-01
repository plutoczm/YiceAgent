"""Single-worker JSONL run metadata; this is not a recovery engine."""

import json
import re
from pathlib import Path

from fastapi import HTTPException

from app.demo.identity import Identity
from app.workflows.models import RunRecord, Step


class RunStore:
    """Append bounded metadata while enforcing ownership on every read."""

    def __init__(self, root: Path):
        """Prepare the metadata-only run directory."""
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, run_id: str) -> Path:
        """Reject traversal before using the run handle as a filename."""
        if re.fullmatch(r"[a-f0-9]{32}", run_id) is None:
            raise HTTPException(404, "run_not_found")
        return self.root / (run_id + ".jsonl")

    def start(self, run_id: str, identity: Identity, workflow: str, parent: str | None) -> None:
        """Create once with private ownership, without request payload."""
        with self.path(run_id).open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": "start",
                        "tenant": identity.tenant,
                        "owner": identity.name,
                        "run_id": run_id,
                        "workflow_id": workflow,
                        "parent_run_id": parent,
                    }
                )
                + "\n"
            )

    def append(self, run_id: str, value: RunRecord | Step) -> None:
        """Accept only controlled models, never arbitrary downstream data."""
        with self.path(run_id).open("a", encoding="utf-8") as stream:
            stream.write(value.model_dump_json() + "\n")

    def read(self, run_id: str, identity: Identity) -> dict[str, object]:
        """Return a sanitized final record or explicit incomplete tail."""
        path = self.path(run_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            first = json.loads(lines[0])
        except (OSError, ValueError, IndexError):
            raise HTTPException(404, "run_not_found") from None
        if first["tenant"] != identity.tenant or (
            not identity.operator and first["owner"] != identity.name
        ):
            raise HTTPException(404, "run_not_found")
        try:
            return RunRecord.model_validate_json(lines[-1]).model_dump(mode="json")
        except ValueError:
            return {"run_id": run_id, "workflow_id": first["workflow_id"], "status": "incomplete"}
