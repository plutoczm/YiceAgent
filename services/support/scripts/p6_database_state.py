"""Read-only public or owned test-schema fingerprint; never emit rows or DSN."""

import hashlib
import json
import sys
from pathlib import Path

from app.demo.fixtures import validate_url
from app.store import Base
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]


def main():
    schema = "public"
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    engine = create_engine(config["database_url"])
    if sys.argv[1:] == ["--test-schema"]:
        from p3_test_database import validate_schema

        state = json.loads((ROOT / ".runtime/demo-processes.json").read_text(encoding="utf-8"))
        runtime = json.loads(Path(state["config"]).read_text(encoding="utf-8"))
        schema = runtime["test_schema"]
        validate_schema(engine, schema)
    elif sys.argv[1:]:
        raise ValueError("unsupported database-state input")
    tables = {}
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            assert connection.scalar(text("SELECT owner FROM public.workflowops_demo_owner")) == (
                "workflowops-demo"
            )
            for name in [*sorted(Base.metadata.tables), "workflowops_demo_owner"]:
                rows = (
                    connection.execute(text(f'SELECT * FROM "{schema}"."{name}"')).mappings().all()
                )
                encoded = sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in rows)
                tables[name] = {
                    "count": len(rows),
                    "sha256": hashlib.sha256("\n".join(encoded).encode()).hexdigest(),
                }
            schemas = (
                connection.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name LIKE 'p2_test_%' OR schema_name LIKE 'p3_test_%' "
                        "ORDER BY schema_name"
                    )
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()
    print(json.dumps({"tables": tables, "test_schemas": schemas}, sort_keys=True))


if __name__ == "__main__":
    main()
