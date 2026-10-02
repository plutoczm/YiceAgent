from pathlib import Path

from backend.app.domain.entities.document import LoadedDocument
from backend.app.infrastructure.document_loaders.docx_loader import DOCXLoader
from backend.app.infrastructure.document_loaders.markdown_loader import MarkdownLoader
from backend.app.infrastructure.document_loaders.pdf_loader import PDFLoader
from backend.app.infrastructure.document_loaders.txt_loader import TXTLoader


class DocumentLoaderFactory:
    def __init__(self) -> None:
        self._loaders = {
            ".txt": TXTLoader(),
            ".md": MarkdownLoader(),
            ".markdown": MarkdownLoader(),
            ".pdf": PDFLoader(),
            ".docx": DOCXLoader(),
        }

    def load(
        self,
        path: Path,
        *,
        domain: str,
        tags: list[str] | None = None,
    ) -> LoadedDocument:
        suffix = path.suffix.lower()
        loader = self._loaders.get(suffix)
        if loader is None:
            raise ValueError(f"Unsupported file type: {suffix}")
        return loader.load(path, domain=domain, tags=tags or [])
