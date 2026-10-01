from backend.app.infrastructure.document_loaders.txt_loader import TXTLoader


class MarkdownLoader(TXTLoader):
    file_type = "markdown"
