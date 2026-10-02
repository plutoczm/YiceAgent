"""Pin a single immutable publication without trusting producer validation."""

import hashlib
import json
import stat
from pathlib import Path

from backend.app.application.business_analytics.errors import BusinessDataDeliveryError
from backend.app.infrastructure.business_analytics.delivery_v2 import (
    FileBusinessDataDeliveryResolver,
)
from pydantic import BaseModel, ConfigDict, Field


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(pattern=r"^v1$")
    tenant_id: str = Field(pattern=r"^tenant-a$")
    export_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    delivery_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_index_key")
        result[key] = value
    return result


def reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (
            part.exists()
            and getattr(part.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise BusinessDataDeliveryError()


class PublishedDeliveryResolver:
    def __init__(self, root: Path):
        reject_links(root)
        self.root = root.resolve()

    def pin(self, tenant: str) -> FileBusinessDataDeliveryResolver:
        try:
            if tenant != "tenant-a":
                raise BusinessDataDeliveryError()
            index = self.root / tenant / "current.json"
            reject_links(index)
            if not index.is_file() or index.stat().st_size > 4096:
                raise BusinessDataDeliveryError()
            publication = Publication.model_validate(
                json.loads(index.read_bytes(), object_pairs_hook=reject_duplicates)
            )
            if publication.tenant_id != tenant:
                raise BusinessDataDeliveryError()
            path = self.root / tenant / publication.export_id
            reject_links(path)
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root / tenant) or not resolved.is_dir():
                raise BusinessDataDeliveryError()
            # Bind the index fingerprint as well as the consumer's independent checks.
            manifest_path = resolved / "manifest.json"
            reject_links(manifest_path)
            if not manifest_path.is_file() or manifest_path.stat().st_size > 1_048_576:
                raise BusinessDataDeliveryError()
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes, object_pairs_hook=reject_duplicates)
            hashes = b"\n".join(
                item["content_sha256"].encode("ascii") for item in manifest["datasets"]
            )
            if (
                hashlib.sha256(manifest_bytes + b"\n" + hashes).hexdigest()
                != publication.delivery_fingerprint
            ):
                raise BusinessDataDeliveryError()
            return FileBusinessDataDeliveryResolver(
                tenant_delivery_directories={tenant: resolved}
            )
        except BusinessDataDeliveryError:
            raise
        except Exception:
            raise BusinessDataDeliveryError() from None
