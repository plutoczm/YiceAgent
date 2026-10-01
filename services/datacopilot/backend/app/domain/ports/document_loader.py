from pathlib import Path
from typing import Protocol

from backend.app.domain.entities.document import LoadedDocument


class DocumentLoader(Protocol):
    def load(
        self,
        path: Path,
        *,
        domain: str,
        tags: list[str] | None = None,
    ) -> LoadedDocument:
        raise NotImplementedError
