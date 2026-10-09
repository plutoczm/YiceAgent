"""Authoritative private history, run ownership, summaries and versioned memories in PG."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from workflowops.config import OWNER
from workflowops.identity import Principal
from workflowops.model_store import ModelError, validate_owned

SCHEMA = "workflowops_agent"


def now():
    return datetime.now(UTC)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class Version(Base):
    __tablename__ = "schema_version"
    owner: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(16))


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(16))
    window: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_run: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("conversation_id", "client_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    client_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    window: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class History(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    position: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = (UniqueConstraint("conversation_id", "range_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    range_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    __tablename__ = "context_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Result(Base):
    __tablename__ = "tool_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Candidate(Base):
    __tablename__ = "memory_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))
    data: Mapped[dict] = mapped_column(JSON)
    expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryCommand(Base):
    __tablename__ = "memory_commands"
    __table_args__ = (UniqueConstraint("tenant", "subject", "client_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(64))
    client_id: Mapped[str] = mapped_column(String(36))
    input_hash: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSON)


def prepare(engine):
    validate_owned(engine)
    with engine.begin() as c:
        c.execute(text("SELECT pg_advisory_xact_lock(2026100504)"))
        present = SCHEMA in inspect(c).get_schema_names()
        existing = set(inspect(c).get_table_names(schema=SCHEMA))
        if present:
            if existing != {t.name for t in Base.metadata.tables.values()}:
                raise ModelError("agent_schema_unowned")
            marker = c.execute(select(Version)).mappings().one_or_none()
            if not marker or marker["owner"] != OWNER or marker["version"] != "1":
                raise ModelError("agent_schema_unowned")
            return
        c.exec_driver_sql("CREATE SCHEMA workflowops_agent")
        Base.metadata.create_all(c)
        c.execute(Version.__table__.insert().values(owner=OWNER, version="1"))


def safe_memory(value):
    if re.search(r"(?i)\bsk-[\w-]{12,}|api.?key|password|bearer|密码|密钥|凭据", value):
        raise ModelError("credential_memory_forbidden")
    if re.search(
        r"订单.*(已支付|退款|已发货|配送中|到账)|支付.*(成功|失败)|审批.*(通过|结果)", value
    ):
        raise ModelError("business_fact_memory_forbidden")


class AgentStore:
    def __init__(self, engine):
        self.engine = engine

    def _conversation(self, s, actor, cid, lock=False):
        q = select(Conversation).where(
            Conversation.id == cid,
            Conversation.tenant == actor.tenant_id,
            Conversation.subject == actor.subject,
        )
        if lock:
            q = q.with_for_update()
        row = s.scalar(q)
        if row is None:
            raise ModelError("conversation_not_found")
        return row

    def conversation_view(self, r):
        return {
            "id": r.id,
            "title": r.title,
            "channel": r.channel,
            "context_window": r.window,
            "version": r.version,
            "active_run": r.active_run,
            "created": r.created.isoformat(),
        }

    def create(self, actor: Principal, body):
        with Session(self.engine) as s, s.begin():
            row = Conversation(
                id=str(uuid4()),
                tenant=actor.tenant_id,
                subject=actor.subject,
                title=body.title,
                channel=body.channel,
                window=body.context_window,
                version=1,
                active_run=None,
                created=now(),
            )
            s.add(row)
            return self.conversation_view(row)

    def conversations(self, actor):
        with Session(self.engine) as s:
            return [
                self.conversation_view(r)
                for r in s.scalars(
                    select(Conversation)
                    .where(
                        Conversation.tenant == actor.tenant_id,
                        Conversation.subject == actor.subject,
                    )
                    .order_by(Conversation.created.desc())
                    .limit(100)
                )
            ]

    def conversation(self, actor, cid):
        with Session(self.engine) as s:
            return self.conversation_view(self._conversation(s, actor, cid))

    def configure(self, actor, cid, body):
        with Session(self.engine) as s, s.begin():
            r = self._conversation(s, actor, cid, True)
            if r.active_run:
                raise ModelError("conversation_busy")
            r.channel, r.window = body.channel, body.context_window
            r.version += 1
            return self.conversation_view(r)

    def begin(self, actor, cid, client_id, payload):
        with Session(self.engine) as s, s.begin():
            c = self._conversation(s, actor, cid, True)
            existing = s.scalar(
                select(Run).where(Run.conversation_id == cid, Run.client_id == client_id)
            )
            if existing:
                if existing.input_hash != digest(payload):
                    raise ModelError("turn_request_conflict")
                return self.run_view(existing), False
            if c.active_run:
                raise ModelError("conversation_busy")
            r = Run(
                id=str(uuid4()),
                conversation_id=cid,
                client_id=client_id,
                input_hash=digest(payload),
                channel=c.channel,
                window=c.window,
                status="running",
                data={"model_calls": 0, "tool_calls": 0},
                created=now(),
                ended=None,
            )
            s.add(r)
            c.active_run = r.id
            return self.run_view(r), True

    def run_view(self, r):
        return {
            "id": r.id,
            "conversation_id": r.conversation_id,
            "client_request_id": r.client_id,
            "channel": r.channel,
            "context_window": r.window,
            "status": r.status,
            **r.data,
            "created": r.created.isoformat(),
            "ended": r.ended.isoformat() if r.ended else None,
        }

    def _run(self, s, actor, rid):
        r = s.get(Run, rid)
        if r is None:
            raise ModelError("run_not_found")
        self._conversation(s, actor, r.conversation_id)
        return r

    def runs(self, actor, cid=None):
        with Session(self.engine) as s:
            if cid is not None:
                self._conversation(s, actor, cid)
            query = (
                select(Run)
                .join(Conversation, Run.conversation_id == Conversation.id)
                .where(
                    Conversation.tenant == actor.tenant_id,
                    Conversation.subject == actor.subject,
                )
            )
            if cid is not None:
                query = query.where(Run.conversation_id == cid)
            return [
                self.run_view(r) for r in s.scalars(query.order_by(Run.created.desc()).limit(100))
            ]

    def run(self, actor, rid):
        with Session(self.engine) as s:
            return self.run_view(self._run(s, actor, rid))

    def update_run(self, rid, **data):
        with Session(self.engine) as s, s.begin():
            r = s.scalar(select(Run).where(Run.id == rid).with_for_update())
            r.data = {**r.data, **data}

    def finish(self, rid, status, **data):
        with Session(self.engine) as s, s.begin():
            r = s.get(Run, rid)
            c = s.scalar(
                select(Conversation).where(Conversation.id == r.conversation_id).with_for_update()
            )
            if r.ended is None:
                r.status, r.ended, r.data = status, now(), {**r.data, **data}
            if c.active_run == rid:
                c.active_run = None

    def recover(self):
        # Only on owned server startup, no replay of interrupted tools or model requests.
        with Session(self.engine) as s, s.begin():
            for c in s.scalars(
                select(Conversation).where(Conversation.active_run.is_not(None)).with_for_update()
            ):
                r = s.get(Run, c.active_run)
                if r and r.ended is None:
                    r.status, r.ended = "interrupted", now()
                    r.data = {
                        **r.data,
                        "reason": "application_restarted",
                        "automatic_replay": False,
                    }
                c.active_run = None

    def append(self, rid, message):
        with Session(self.engine) as s, s.begin():
            r = s.get(Run, rid)
            # Per-run append is single-owner; lock serializes position allocation.
            if r is None:
                raise ModelError("run_not_found")
            c = s.scalar(
                select(Conversation).where(Conversation.id == r.conversation_id).with_for_update()
            )
            previous = s.scalars(
                select(History.position)
                .where(History.conversation_id == c.id)
                .order_by(History.position.desc())
                .limit(1)
            ).first()
            row = History(
                id=str(uuid4()),
                conversation_id=c.id,
                run_id=rid,
                position=(previous or 0) + 1,
                data=message,
                created=now(),
            )
            s.add(row)
            return {"id": row.id, "run_id": rid, "position": row.position, **message}

    def history(self, actor, cid):
        with Session(self.engine) as s:
            self._conversation(s, actor, cid)
            return [
                {"id": r.id, "run_id": r.run_id, "position": r.position, **r.data}
                for r in s.scalars(
                    select(History).where(History.conversation_id == cid).order_by(History.position)
                )
            ]

    def summary(self, actor, cid):
        with Session(self.engine) as s:
            self._conversation(s, actor, cid)
            r = s.scalars(
                select(Summary)
                .where(Summary.conversation_id == cid)
                .order_by(Summary.version.desc())
                .limit(1)
            ).first()
            return (
                {"id": r.id, "version": r.version, "range_hash": r.range_hash, **r.data}
                if r
                else None
            )

    def save_summary(self, actor, cid, range_hash, data):
        with Session(self.engine) as s, s.begin():
            self._conversation(s, actor, cid, True)
            r = s.scalar(
                select(Summary).where(
                    Summary.conversation_id == cid, Summary.range_hash == range_hash
                )
            )
            if r is None:
                last = (
                    s.scalars(
                        select(Summary.version)
                        .where(Summary.conversation_id == cid)
                        .order_by(Summary.version.desc())
                        .limit(1)
                    ).first()
                    or 0
                )
                r = Summary(
                    id=str(uuid4()),
                    conversation_id=cid,
                    version=last + 1,
                    range_hash=range_hash,
                    data=data,
                    created=now(),
                )
                s.add(r)
            return {"id": r.id, "version": r.version, "range_hash": r.range_hash, **r.data}

    def add_private(self, table, rid, data):
        with Session(self.engine) as s, s.begin():
            row = table(id=str(uuid4()), run_id=rid, data=data, created=now())
            s.add(row)
            return row.id

    def private(self, actor, rid, table):
        with Session(self.engine) as s:
            self._run(s, actor, rid)
            return [
                {"id": r.id, **r.data}
                for r in s.scalars(
                    select(table).where(table.run_id == rid).order_by(table.created, table.id)
                )
            ]

    def candidate(self, actor, cid, mid, item, model, correction=False):
        safe_memory(item.quote)
        with Session(self.engine) as s, s.begin():
            self._conversation(s, actor, cid, True)
            message = s.get(History, mid)
            if (
                not message
                or message.conversation_id != cid
                or item.quote not in (message.data.get("content") or "")
            ):
                raise ModelError("memory_source_invalid")
            if correction:
                for old in s.scalars(
                    select(Candidate).where(
                        Candidate.tenant == actor.tenant_id,
                        Candidate.subject == actor.subject,
                        Candidate.kind == item.kind,
                        Candidate.status == "pending",
                    )
                ):
                    old.status = "invalidated"
                    old.version += 1
            r = Candidate(
                id=str(uuid4()),
                tenant=actor.tenant_id,
                subject=actor.subject,
                key=item.key,
                kind=item.kind,
                status="pending",
                version=1,
                data={
                    "text": item.quote,
                    "source_message_id": mid,
                    "conversation_id": cid,
                    "model_id": model,
                },
                created=now(),
            )
            s.add(r)
            return self.memory_view(r)

    def memory_view(self, r):
        return {
            "id": r.id,
            "key": r.key,
            "version": r.version,
            "status": r.status,
            **r.data,
            "expires": r.expires.isoformat() if isinstance(r, Memory) and r.expires else None,
        }

    def memories(self, actor, candidates=False):
        table = Candidate if candidates else Memory
        with Session(self.engine) as s:
            q = select(table).where(table.tenant == actor.tenant_id, table.subject == actor.subject)
            if not candidates:
                q = q.where(
                    Memory.status == "active", (Memory.expires.is_(None) | (Memory.expires > now()))
                )
            return [
                self.memory_view(r) for r in s.scalars(q.order_by(table.created.desc()).limit(100))
            ]

    def memory_command(self, actor, target, body, operation):
        value = {"target": target, "operation": operation, **body.model_dump()}
        with Session(self.engine) as s, s.begin():
            # Principal-level lock serializes commands and prevents double confirmation.
            lock_id = int(digest([actor.tenant_id, actor.subject])[:15], 16)
            s.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
            old = s.scalar(
                select(MemoryCommand).where(
                    MemoryCommand.tenant == actor.tenant_id,
                    MemoryCommand.subject == actor.subject,
                    MemoryCommand.client_id == body.client_request_id,
                )
            )
            if old:
                if old.input_hash != digest(value):
                    raise ModelError("memory_request_conflict")
                return old.data
            table = Candidate if operation == "confirm" else Memory
            r = s.scalar(
                select(table)
                .where(
                    table.id == target,
                    table.tenant == actor.tenant_id,
                    table.subject == actor.subject,
                )
                .with_for_update()
            )
            if r is None:
                raise ModelError("memory_not_found")
            if r.version != body.expected_version:
                raise ModelError("memory_version_conflict")
            if operation == "confirm":
                if r.status != "pending":
                    raise ModelError("memory_candidate_inactive")
                r.status = "confirmed" if body.confirm else "rejected"
                r.version += 1
                if body.confirm:
                    for prior in s.scalars(
                        select(Memory).where(
                            Memory.tenant == actor.tenant_id,
                            Memory.subject == actor.subject,
                            Memory.key == r.key,
                            Memory.status == "active",
                        )
                    ):
                        prior.status = "superseded"
                    m = Memory(
                        id=str(uuid4()),
                        tenant=r.tenant,
                        subject=r.subject,
                        key=r.key,
                        version=1,
                        status="active",
                        data={
                            **r.data,
                            "candidate_id": r.id,
                            "kind": r.kind,
                            "index_status": "pending",
                            "versions": [],
                        },
                        expires=None,
                        created=now(),
                    )
                    s.add(m)
                    result = self.memory_view(m)
                else:
                    result = self.memory_view(r)
            else:
                if r.status != "active":
                    raise ModelError("memory_inactive")
                prior = {"version": r.version, "text": r.data["text"], "changed": now().isoformat()}
                r.version += 1
                if operation == "edit":
                    safe_memory(body.text)
                    r.data = {
                        **r.data,
                        "text": body.text,
                        "versions": [*r.data.get("versions", []), prior],
                        "index_status": "pending",
                        "edited_by_user": True,
                    }
                    r.expires = (
                        now() + timedelta(days=body.expires_in_days)
                        if body.expires_in_days
                        else None
                    )
                else:
                    r.status = "deleted"
                    r.data = {
                        **r.data,
                        "versions": [*r.data.get("versions", []), prior],
                        "index_status": "delete_pending",
                    }
                result = self.memory_view(r)
            s.add(
                MemoryCommand(
                    id=str(uuid4()),
                    tenant=actor.tenant_id,
                    subject=actor.subject,
                    client_id=body.client_request_id,
                    input_hash=digest(value),
                    data=result,
                )
            )
            return result

    def invalidate_candidates(self, actor, cid):
        with Session(self.engine) as s, s.begin():
            self._conversation(s, actor, cid, True)
            for row in s.scalars(
                select(Candidate).where(
                    Candidate.tenant == actor.tenant_id,
                    Candidate.subject == actor.subject,
                    Candidate.status == "pending",
                )
            ):
                row.status = "invalidated"
                row.version += 1

    def calibration(self, actor, channel):
        with Session(self.engine) as s:
            values = s.scalars(
                select(Run)
                .join(Conversation, Run.conversation_id == Conversation.id)
                .where(
                    Conversation.tenant == actor.tenant_id,
                    Conversation.subject == actor.subject,
                    Run.channel == channel,
                    Run.ended.is_not(None),
                )
                .order_by(Run.created.desc())
                .limit(20)
            )
            return max([1.2] + [float(r.data.get("estimator_factor", 1.2)) for r in values])

    def indexed(self, actor, mid, version, status, index_id=None):
        with Session(self.engine) as s, s.begin():
            r = s.scalar(
                select(Memory)
                .where(
                    Memory.id == mid,
                    Memory.tenant == actor.tenant_id,
                    Memory.subject == actor.subject,
                )
                .with_for_update()
            )
            if r and r.version == version:
                r.data = {**r.data, "index_status": status, "index_id": index_id}

    def result(self, actor, cid, result_id):
        with Session(self.engine) as s:
            self._conversation(s, actor, cid)
            row = s.get(Result, result_id)
            if row is None or not row.data.get("tool"):
                raise ModelError("tool_result_not_found")
            run = self._run(s, actor, row.run_id)
            if run.conversation_id != cid:
                raise ModelError("tool_result_not_found")
            return row.data["value"]

    def memory(self, actor, mid):
        with Session(self.engine) as s:
            row = s.scalar(
                select(Memory).where(
                    Memory.id == mid,
                    Memory.tenant == actor.tenant_id,
                    Memory.subject == actor.subject,
                )
            )
            return self.memory_view(row) if row else None

    def resolve_memories(self, actor, matches):
        # Never trust vector payloads for content/status/ownership.
        active = {r["id"]: r for r in self.memories(actor)}
        return [
            active[mid]
            for mid, version in matches
            if mid in active and active[mid]["version"] == version
        ][:5]
