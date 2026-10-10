"""Dedicated PostgreSQL ownership boundary. No v1 DSN or implicit schema creation."""

from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect, select
from sqlalchemy.engine import URL, Engine

from yice_agent.config import OWNER, Settings

metadata = MetaData()
owner_table = Table("schema_owner", metadata, Column("owner", String(64), primary_key=True))


def make_engine(settings: Settings, schema: str = "public") -> Engine:
    import re

    if schema != "public" and re.fullmatch(r"v2_test_[a-f0-9]{32}", schema) is None:
        raise ValueError("business_schema_not_owned")
    url = URL.create(
        "postgresql+psycopg",
        username="workflowops_v2",
        password=settings.database_password.get_secret_value(),
        host="127.0.0.1",
        port=55433,
        database="workflowops_v2",
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": f"-csearch_path={schema}"},
        hide_parameters=True,
    )


def initialize_database(engine: Engine) -> None:
    """Explicit provisioning gate calls this only after resource ownership validation."""
    if (
        engine.dialect.name != "postgresql"
        or engine.url.host != "127.0.0.1"
        or engine.url.port != 55433
        or engine.url.database != "workflowops_v2"
    ):
        raise ValueError("database_not_owned_v2")
    with engine.begin() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(2026100202)")
        tables = inspect(connection).get_table_names()
        if tables:
            if "schema_owner" not in tables:
                raise ValueError("database_ownership_missing")
            values = connection.execute(select(owner_table.c.owner)).scalars().all()
            if values != [OWNER]:
                raise ValueError("database_ownership_invalid")
            return
        owner_table.create(connection)
        connection.execute(owner_table.insert().values(owner=OWNER))


def check_database(engine: Engine) -> bool:
    with engine.connect() as connection:
        return connection.execute(select(owner_table.c.owner)).scalars().all() == [OWNER]
