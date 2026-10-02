from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build_container
from app.main import create_app


def build_ui_client(tmp_path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ui.db'}",
        metrics_enabled=False,
        tracing_enabled=False,
        app_env="development",
        auth_mode="dev",
        mcp_auth_mode="disabled",
        seed_demo_data=True,
    )
    return TestClient(create_app(build_container(settings)))


def test_root_redirects_to_local_ui(tmp_path):
    client = build_ui_client(tmp_path)
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_local_ui_and_assets_are_served(tmp_path):
    client = build_ui_client(tmp_path)

    page = client.get("/ui/")
    stylesheet = client.get("/ui/styles.css")
    script = client.get("/ui/app.js")

    assert page.status_code == 200
    assert "SupportOps AI · 本地工作台" in page.text
    assert "用户会话" in page.text
    assert "客服工单" in page.text
    assert stylesheet.status_code == 200
    assert "--accent" in stylesheet.text
    assert script.status_code == 200
    assert "/v1/support/messages" in script.text
    assert "/v1/agent/tickets/" in script.text
    assert "/v1/conversations/" in script.text
    assert "发送人工回复" in script.text
