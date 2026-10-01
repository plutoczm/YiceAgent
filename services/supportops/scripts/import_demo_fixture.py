"""Only the root-owned runtime configuration selects the database."""

import argparse
import json

from sqlalchemy import create_engine

from app.demo.fixtures import ROOT, import_fixture, load_fixture, validate_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-demo", action="store_true")
    args = parser.parse_args()
    fixture, digest = load_fixture(ROOT / ".runtime/demo-fixture")
    config = json.loads((ROOT / ".runtime/demo-db.json").read_text(encoding="utf-8"))
    validate_url(config["database_url"])
    engine = create_engine(config["database_url"])
    try:
        print(json.dumps(import_fixture(engine, fixture, digest, args.reset_demo)))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
