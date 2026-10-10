"""PostgreSQL is authoritative for active sources; Qdrant payload never authorizes evidence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    MetaData,
    String,
    Text,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from yice_agent.config import OWNER
from yice_agent.identity import Principal
from yice_agent.model_store import ModelError, validate_owned

SCHEMA = "workflowops_knowledge"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Version(Base):
    __tablename__ = "schema_version"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))


class Index(Base):
    __tablename__ = "indexes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection: Mapped[str] = mapped_column(String(128))
    corpus_version: Mapped[str] = mapped_column(String(128))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_revision: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    chunk_count: Mapped[int] = mapped_column(Integer)
    created: Mapped[Any] = mapped_column(DateTime(timezone=True))


class Chunk(Base):
    __tablename__ = "chunks"
    point_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    index_id: Mapped[str] = mapped_column(String(64), index=True)
    chunk_id: Mapped[str] = mapped_column(String(128))
    document_id: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(1000))
    license: Mapped[str] = mapped_column(String(300))
    document_hash: Mapped[str] = mapped_column(String(64))
    chunk_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    tokens: Mapped[int] = mapped_column(Integer)
    facts: Mapped[dict[str, str]] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Active(Base):
    __tablename__ = "active_index"
    tenant: Mapped[str] = mapped_column(String(64), primary_key=True)
    index_id: Mapped[str] = mapped_column(String(64))


def prepare(engine: Engine) -> None:
    validate_owned(engine)
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(2026100405)"))
        was_present = "workflowops_knowledge" in inspect(c).get_schema_names()
        c.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS workflowops_knowledge")
        existing = set(inspect(c).get_table_names(schema=SCHEMA))
        if was_present and not existing:
            raise ModelError("unmarked_schema_unowned")
        if existing:
            if existing != {t.name for t in Base.metadata.tables.values()}:
                raise ModelError("knowledge_schema_unowned")
            marker = c.execute(select(Version)).mappings().one_or_none()
            if marker is None or marker["owner"] != OWNER or marker["version"] != "1":
                raise ModelError("knowledge_schema_unowned")
            return
        Base.metadata.create_all(c)
        c.execute(Version.__table__.insert().values(owner=OWNER, version="1"))


class Store:
    def __init__(self, engine: Engine):
        self.engine = engine

    def index(self, index_id: str) -> dict[str, Any] | None:
        with Session(self.engine) as s:
            row = s.get(Index, index_id)
            if row is None:
                return None
            return {c.name: getattr(row, c.name) for c in row.__table__.columns}

    def create(self, values: dict[str, Any], rows: list[dict[str, Any]], *, activate: bool):
        with Session(self.engine) as s, s.begin():
            s.execute(text("SELECT pg_advisory_xact_lock(2026100406)"))
            existing = s.get(Index, values["id"])
            if existing:
                if (
                    existing.manifest_hash != values["manifest_hash"]
                    or existing.embedding_revision != values["embedding_revision"]
                ):
                    raise ModelError("index_version_conflict")
                if existing.status == "ready":
                    return
                s.delete(existing)
                s.flush()
            s.add(Index(**values, status="ready", created=datetime.now(UTC)))
            for row in rows:
                old = s.get(Chunk, row["point_id"])
                if old and old.chunk_hash != row["chunk_hash"]:
                    raise ModelError("chunk_version_conflict")
                if old is None:
                    s.add(Chunk(**row))
            if activate:
                for tenant in ("tenant-a", "tenant-b"):
                    marker = s.get(Active, tenant)
                    if marker:
                        marker.index_id = values["id"]
                    else:
                        s.add(Active(tenant=tenant, index_id=values["id"]))

    def read(
        self, actor: Principal, index_id: str | None = None
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with Session(self.engine) as s:
            if index_id is None:
                active = s.get(Active, actor.tenant_id)
                if active is None:
                    raise ModelError("knowledge_not_indexed")
                index_id = active.index_id
            info = self.index(index_id)
            if info is None or info["status"] != "ready":
                raise ModelError("knowledge_index_unavailable")
            rows = s.scalars(
                select(Chunk).where(
                    Chunk.tenant == actor.tenant_id,
                    Chunk.index_id == index_id,
                    Chunk.active.is_(True),
                )
            ).all()
            return info, [
                {c.name: getattr(row, c.name) for c in row.__table__.columns} for row in rows
            ]

    def status(self, actor: Principal):
        info, rows = self.read(actor)
        return {
            "index_id": info["id"],
            "corpus_version": info["corpus_version"],
            "collection": info["collection"],
            "embedding_model": info["embedding_model"],
            "embedding_revision": info["embedding_revision"],
            "active_chunks": len(rows),
            "documents": list(
                {
                    r["document_id"]: {
                        "id": r["document_id"],
                        "version": r["version"],
                        "title": r["title"],
                        "source": r["source"],
                        "license": r["license"],
                        "sha256": r["document_hash"],
                    }
                    for r in rows
                }.values()
            ),
        }
