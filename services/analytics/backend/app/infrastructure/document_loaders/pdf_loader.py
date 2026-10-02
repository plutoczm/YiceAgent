from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader

from backend.app.application.rag.chunking_service import clean_text
from backend.app.domain.entities.document import LoadedDocument


class PDFLoader:
    file_type = "pdf"

    def load(
        self,
        path: Path,
        *,
        domain: str,
        tags: list[str] | None = None,
    ) -> LoadedDocument:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return LoadedDocument(
            document_id=uuid4().hex,
            filename=path.name,
            file_type=self.file_type,
            domain=domain,
            source_path=path,
            stored_path=path,
            content=clean_text(text),
            tags=tags or [],
        )
