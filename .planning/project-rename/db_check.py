"""Read-only before/after checks; database contents and credentials are never printed."""

import hashlib
import json
import sys
from pathlib import Path

import httpx
from sqlalchemy import text

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "src"))
if (root / "src/yice_agent").is_dir():
    from yice_agent.config import Settings
    from yice_agent.database import make_engine
else:
    from workflowops.config import Settings
    from workflowops.database import make_engine


def snapshot():
    settings = Settings.load()
    engine = make_engine(settings)
    tables = {}
    with engine.connect() as c:
        rows = c.execute(text("SELECT table_schema,table_name FROM information_schema.tables WHERE table_type='BASE TABLE' AND (table_schema='public' OR table_schema LIKE 'workflowops%') ORDER BY table_schema,table_name")).all()
        for schema, table in rows:
            statement = f'SELECT row_to_json(t)::text FROM "{schema}"."{table}" AS t'
            values = sorted(c.exec_driver_sql(statement).scalars().all())
            tables[schema + "." + table] = {
                "rows": len(values),
                "sha256": hashlib.sha256("\n".join(values).encode()).hexdigest(),
            }
        temporary = c.execute(text("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE :pattern"), {"pattern": "v2_test_%"}).scalars().all()
    engine.dispose()
    with httpx.Client(base_url="http://127.0.0.1:6335", headers={"api-key": settings.qdrant_key.get_secret_value()}, trust_env=False) as client:
        names = sorted(x["name"] for x in client.get("/collections").json()["result"]["collections"])
        collections = {}
        for name in names:
            value = client.get("/collections/" + name).json()["result"]
            collections[name] = {k: value[k] for k in ("points_count", "config")}
    return {"tables": tables, "collections": collections, "temporary_schemas": temporary}


out = root / ".runtime/v2/project-rename"
value = snapshot()
target = out / ("database-before.json" if sys.argv[1] == "before" else "database-after.json")
target.write_text(json.dumps(value, indent=2), encoding="utf-8")
same = None if sys.argv[1] == "before" else value == json.loads((out / "database-before.json").read_text())
print(json.dumps({"tables": len(value["tables"]), "collections": len(value["collections"]), "unchanged": same, "temporary_schemas": len(value["temporary_schemas"])}))
if same is False:
    raise SystemExit(1)
