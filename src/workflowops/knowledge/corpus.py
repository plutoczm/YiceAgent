"""UTF-8 Markdown structure parsing and complete-block chunking with BGE token counts."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from workflowops.config import ROOT
from workflowops.model_store import ModelError


@dataclass(frozen=True)
class Document:
    id: str
    version: str
    title: str
    source: str
    license: str
    content: str
    sha256: str
    facts: dict[str, str]


def load_corpus(name: str = "demo-cn") -> tuple[str, list[Document]]:
    if name not in {"demo-cn", "challenge-cn"}:
        raise ModelError("unknown_corpus")
    directory = ROOT / "data/knowledge"
    manifest = json.loads((directory / (name + ".json")).read_text(encoding="utf-8"))
    docs = []
    for row in manifest["documents"]:
        path = (directory / row["file"]).resolve()
        if not path.is_relative_to(directory.resolve()) or path.suffix != ".md":
            raise ModelError("invalid_document_path")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != row["sha256"]:
            raise ModelError("document_hash_mismatch")
        docs.append(
            Document(
                id=row["id"],
                version=row["version"],
                title=row["title"],
                source=row["source"],
                license=row["license"],
                content=data.decode("utf-8"),
                sha256=digest,
                facts=row.get("facts", {}),
            )
        )
    if not docs or len({d.id for d in docs}) != len(docs):
        raise ModelError("invalid_corpus_manifest")
    return manifest["version"], docs


def blocks(document: Document) -> list[str]:
    lines = document.content.splitlines()
    result, paragraph = [], []
    heading = document.title

    def flush():
        if paragraph:
            result.append(heading + "\n" + "\n".join(paragraph))
            paragraph.clear()

    for line in lines:
        if re.match(r"^#{1,6} ", line):
            flush()
            heading = line.lstrip("# ")
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
    flush()
    return result


def chunks(
    document: Document, parts: list[str], counts: list[int], target: int = 400, overlap: int = 60
) -> list[dict[str, Any]]:
    if len(parts) != len(counts) or any(n > 1024 for n in counts):
        raise ModelError("document_block_capacity_exceeded")
    output, current, tokens = [], [], 0

    def publish():
        text = "\n\n".join(parts[i] for i in current)
        output.append(
            {
                "id": document.id + ".c" + str(len(output) + 1).zfill(3),
                "document_id": document.id,
                "content": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "tokens": tokens,
            }
        )

    for i, count in enumerate(counts):
        if current and tokens + count > target:
            publish()
            tail, size = [], 0
            for index in reversed(current):
                if size + counts[index] > overlap:
                    break
                tail.insert(0, index)
                size += counts[index]
            current, tokens = tail, size
        current.append(i)
        tokens += count
    if current:
        publish()
    return output


def manifest_hash(version: str, documents: list[Document], model_revision: str) -> str:
    wire = {
        "version": version,
        "documents": [asdict(d) for d in documents],
        "embedding_revision": model_revision,
        "chunker": "markdown-block-bge400-overlap60-v1",
    }
    return hashlib.sha256(json.dumps(wire, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
