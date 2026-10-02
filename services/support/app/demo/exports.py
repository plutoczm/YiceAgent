"""Demo-only immutable publication; SupportOps alone owns the index."""

import json
import os
import stat
import threading
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import Principal
from app.business_data.export_v1 import write_business_data_export_v1
from app.business_data.models import DeliveryContext
from app.business_data.validate_v1 import validate_business_data_delivery_v1
from app.main import get_principal


class EmptyExport(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    delivery_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    generated_at: str
    dataset_row_counts: dict[str, int]
    source: str = "supportops_delivery_v2"


def safe_directory(path: Path) -> Path:
    for part in (path, *path.parents):
        if part.is_symlink() or (
            part.exists()
            and getattr(part.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("linked_publication_directory")
    return path.resolve()


class DemoPublisher:
    def __init__(self, engine, root: Path):
        self.engine = engine
        self.root = safe_directory(root)
        self.lock = threading.Lock()

    def publish(self, tenant: str) -> ExportView:
        # Tenant comes only from the fixed demo principal, never the request.
        if tenant != "tenant-a":
            raise ValueError("unconfigured_export_tenant")
        with self.lock:
            directory = safe_directory(self.root / tenant)
            directory.mkdir(parents=True, exist_ok=True)
            export_id = uuid4().hex
            destination = directory / export_id
            write_business_data_export_v1(self.engine, DeliveryContext(tenant, "CNY"), destination)
            validated = validate_business_data_delivery_v1(destination)
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            if validated.tenant_id != tenant:
                raise ValueError("publication_tenant_mismatch")
            index = directory / "current.json"
            safe_directory(index)
            temporary = directory / (".index-" + uuid4().hex)
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "version": "v1",
                            "tenant_id": tenant,
                            "export_id": export_id,
                            "delivery_fingerprint": validated.delivery_fingerprint,
                        },
                        stream,
                        sort_keys=True,
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, index)
            finally:
                temporary.unlink(missing_ok=True)
            return ExportView(
                snapshot_id=validated.snapshot["snapshot_id"],
                delivery_fingerprint=validated.delivery_fingerprint,
                generated_at=manifest["generated_at"],
                dataset_row_counts=validated.dataset_row_counts,
            )


def export_router(publisher: DemoPublisher) -> APIRouter:
    router = APIRouter()

    @router.post("/internal/demo/exports", response_model=ExportView)
    def export(body: EmptyExport, current: Annotated[Principal, Depends(get_principal)]):
        if current.authentication_method != "demo-opaque" or "support_admin" not in current.roles:
            raise HTTPException(403, "operator_required")
        try:
            return publisher.publish(current.tenant_id)
        except Exception:
            # Never expose paths, database exceptions, or unvalidated manifest content.
            raise HTTPException(503, "snapshot_publication_failed") from None

    return router
