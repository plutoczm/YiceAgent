from pathlib import Path
from uuid import uuid4

from backend.app.application.rag.chunking_service import clean_text
from backend.app.domain.entities.document import LoadedDocument


class TXTLoader:
    file_type = "txt"

    def load(
        self,
        path: Path,
        *,
        domain: str,
        tags: list[str] | None = None,
    ) -> LoadedDocument:
        content = path.read_text(encoding="utf-8")
        return LoadedDocument(
            document_id=uuid4().hex,
            filename=path.name,
            file_type=self.file_type,
            domain=domain,
            source_path=path,
            stored_path=path,
            content=clean_text(content),
            tags=tags or [],
        )
