"""Publishing failures leave the previous index and business state untouched."""

import json
import secrets

import pytest
from fastapi.testclient import TestClient

from app.demo.composition import build_demo_container, create_demo_app
from app.demo.exports import DemoPublisher
from app.demo.identity import PERSONAS
from app.store import SupportStore


@pytest.fixture
def setup(tmp_path):
    store = SupportStore("sqlite:///" + str(tmp_path / "test.db"))
    store.create_schema()
    root = tmp_path / "deliveries"
    tokens = {name: secrets.token_urlsafe(32) for name in PERSONAS}
    with TestClient(create_demo_app(build_demo_container(store, tokens), root)) as client:
        yield store, root, tokens, client
    store.engine.dispose()


def test_export_auth_closed_scope_and_immutable_publication(setup):
    store, root, tokens, client = setup
    for name in PERSONAS:
        response = client.post(
            "/internal/demo/exports", json={}, headers={"Authorization": "Bearer " + tokens[name]}
        )
        assert response.status_code == (200 if name == "operator-a" else 403)
    headers = {"Authorization": "Bearer " + tokens["operator-a"]}
    for field in ("tenant_id", "path", "currency", "policy"):
        assert (
            client.post(
                "/internal/demo/exports", json={field: "untrusted"}, headers=headers
            ).status_code
            == 422
        )
    first_index = json.loads((root / "tenant-a/current.json").read_bytes())
    first = root / "tenant-a" / first_index["export_id"]
    original = {p.name: p.read_bytes() for p in first.iterdir()}
    second = client.post("/internal/demo/exports", json={}, headers=headers)
    assert second.status_code == 200
    assert json.loads((root / "tenant-a/current.json").read_bytes())["export_id"] != first.name
    assert original == {p.name: p.read_bytes() for p in first.iterdir()}
    assert str(root) not in second.text


@pytest.mark.parametrize("failure", ["export", "validation", "index"])
def test_failed_refresh_preserves_index(setup, monkeypatch, failure):
    store, root, _, _ = setup
    publisher = DemoPublisher(store.engine, root)
    publisher.publish("tenant-a")
    index = root / "tenant-a/current.json"
    original = index.read_bytes()
    from app.demo import exports

    def fail(*args, **kwargs):
        raise RuntimeError("private path/database error")

    if failure == "export":
        monkeypatch.setattr(exports, "write_business_data_export_v1", fail)
    elif failure == "validation":
        monkeypatch.setattr(exports, "validate_business_data_delivery_v1", fail)
    else:
        monkeypatch.setattr(exports.os, "replace", fail)
    with pytest.raises(RuntimeError):
        publisher.publish("tenant-a")
    assert index.read_bytes() == original
