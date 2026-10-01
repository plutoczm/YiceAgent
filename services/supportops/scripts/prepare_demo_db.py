"""Provision ownership on a verified fresh Docker DB, then run service Alembic."""

import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.demo.fixtures import OWNER, ROOT, validate_url


def main():
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    engine = create_engine(config["database_url"])
    with engine.begin() as connection:
        tables = inspect(connection).get_table_names()
        if not tables:
            connection.execute(
                text(
                    "CREATE TABLE workflowops_demo_owner "
                    "(owner TEXT PRIMARY KEY, fixture_hash TEXT)"
                )
            )
            connection.execute(
                text("INSERT INTO workflowops_demo_owner(owner) VALUES (:owner)"), {"owner": OWNER}
            )
        elif "workflowops_demo_owner" not in tables or connection.execute(
            text("SELECT owner FROM workflowops_demo_owner")
        ).scalars().all() != [OWNER]:
            raise ValueError("refuse migration of unowned database")
    engine.dispose()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", config["database_url"].replace("%", "%%"))
    # Do not let a caller's original-service DATABASE_URL select the migration target.
    import os

    os.environ["DATABASE_URL"] = config["database_url"]
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
