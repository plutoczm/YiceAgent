import importlib
import runpy
import subprocess
import sys
from pathlib import Path

import httpx

from frontend.services.backend_client import BackendClient


class StubResponse:
    def __init__(self, payload=None, status_code: int = 200, text: str = "") -> None:
        self.payload = payload or {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "http://backend"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.payload

    def iter_lines(self):
        yield 'event: token'
        yield 'data: {"text": "hello"}'
        yield ''
        yield 'event: citations'
        yield 'data: {"citations": []}'
        yield ''


class StubHTTPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs))
        if path == "/health":
            return StubResponse({"status": "ok"})
        if path == "/api/v1/knowledge/documents":
            return StubResponse({"documents": []})
        return StubResponse({})

    def post(self, path: str, **kwargs):
        self.calls.append(("POST", path, kwargs))
        if path == "/api/v1/agent/chat":
            return StubResponse(
                {
                    "intent": "GENERAL_CHAT",
                    "final_response": "done",
                    "result": {"answer": "done"},
                    "routing_path": ["classify_intent", "planner", "general_chat", "format_response"],
                    "metadata": {},
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )
        return StubResponse({"ok": True, "sql": "SELECT 1", "answer": "done"})

    def delete(self, path: str, **kwargs):
        self.calls.append(("DELETE", path, kwargs))
        return StubResponse({"deleted": True})

    def stream(self, method: str, path: str, **kwargs):
        self.calls.append((method, path, kwargs))
        return _StreamContext(StubResponse())


class _StreamContext:
    def __init__(self, response: StubResponse) -> None:
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, exc_type, exc, tb):
        return False


def test_backend_client_defaults_to_docker_backend_url(monkeypatch) -> None:
    monkeypatch.delenv("BACKEND_URL", raising=False)

    client = BackendClient()

    assert client.base_url == "http://backend:8000"


def test_backend_client_ignores_proxy_environment_by_default(monkeypatch) -> None:
    created_clients: list[dict] = []

    class RecordingHTTPClient:
        def __init__(self, **kwargs) -> None:
            created_clients.append(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:3505")
    monkeypatch.setattr("frontend.services.backend_client.httpx.Client", RecordingHTTPClient)

    client = BackendClient(base_url="http://127.0.0.1:8000")
    client.close()

    assert created_clients[0]["trust_env"] is False


def test_backend_client_calls_expected_api_paths() -> None:
    http_client = StubHTTPClient()
    client = BackendClient(http_client=http_client)

    assert client.health()["status"] == "ok"
    client.list_documents()
    client.query_knowledge("What is AQE?")
    client.text2sql("统计GMV", "hive", "orders(order_id bigint)")
    client.sql_review("SELECT * FROM t", "spark")
    client.warehouse_design("设计电商订单分析数仓", use_rag=True)
    client.agent_chat("介绍一下你")
    chunks = list(client.stream_chat("hello"))
    agent_chunks = list(client.stream_agent_chat("hello"))

    paths = [call[1] for call in http_client.calls]
    assert "/health" in paths
    assert "/api/v1/knowledge/documents" in paths
    assert "/api/v1/knowledge/query" in paths
    assert "/api/v1/text2sql" in paths
    assert "/api/v1/sql-review" in paths
    assert "/api/v1/warehouse-design" in paths
    assert "/api/v1/agent/chat" in paths
    assert "/api/v1/agent/chat/stream" in paths
    assert "/api/v1/chat/stream" in paths
    assert chunks[0]["event"] == "token"
    assert chunks[0]["data"]["text"] == "hello"
    assert agent_chunks[0]["event"] == "token"


def test_streamlit_pages_expose_render_functions() -> None:
    modules = [
        "frontend.app",
        "frontend.pages.chat",
        "frontend.pages.knowledge_base",
        "frontend.pages.text2sql",
        "frontend.pages.sql_review",
        "frontend.pages.warehouse_design",
        "frontend.components.sidebar",
        "frontend.components.api_client",
        "frontend.components.chat_message",
    ]

    for module_name in modules:
        module = importlib.import_module(module_name)
        assert hasattr(module, "render") or hasattr(module, "main") or hasattr(module, "get_client")

    theme = importlib.import_module("frontend.components.theme")
    assert hasattr(theme, "apply_theme")
    assert hasattr(theme, "hero")
    assert hasattr(theme, "card_grid")
    assert hasattr(theme, "quick_actions")
    assert hasattr(theme, "recent_activity")
    assert hasattr(theme, "record_activity")


def test_streamlit_page_scripts_import_when_run_from_pages_directory(monkeypatch) -> None:
    project_root = Path.cwd().resolve()
    page_files = [
        project_root / "frontend/pages/chat.py",
        project_root / "frontend/pages/knowledge_base.py",
        project_root / "frontend/pages/text2sql.py",
        project_root / "frontend/pages/sql_review.py",
        project_root / "frontend/pages/warehouse_design.py",
    ]

    original_path = list(sys.path)
    path_without_project_root = [
        path
        for path in original_path
        if Path(path or ".").resolve() != project_root.resolve()
    ]
    for page_file in page_files:
        monkeypatch.setattr(sys, "path", [str(page_file.parent), *path_without_project_root])
        for module_name in list(sys.modules):
            if module_name == "frontend" or module_name.startswith("frontend."):
                monkeypatch.delitem(sys.modules, module_name, raising=False)
        try:
            module_globals = runpy.run_path(str(page_file), run_name=f"test_{page_file.stem}")
        finally:
            sys.path = list(original_path)

        assert callable(module_globals["render"])


def test_streamlit_page_scripts_import_in_clean_subprocess_without_project_root() -> None:
    project_root = Path.cwd().resolve()
    script = """
import runpy
import sys
from pathlib import Path

project_root = Path.cwd().resolve()
page_file = project_root / "frontend/pages/knowledge_base.py"
sys.path = [
    str(page_file.parent),
    *[
        path
        for path in sys.path
        if Path(path or ".").resolve() != project_root
    ],
]
runpy.run_path(str(page_file), run_name="streamlit_page")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_frontend_dockerfile_exposes_streamlit_port() -> None:
    dockerfile = Path("frontend/Dockerfile")

    assert dockerfile.is_file()
    content = dockerfile.read_text(encoding="utf-8")
    assert "EXPOSE 8501" in content
    assert "streamlit" in content
    assert "frontend/app.py" in content


def test_frontend_uses_chinese_navigation_and_theme() -> None:
    app_content = Path("frontend/app.py").read_text(encoding="utf-8")
    theme_content = Path("frontend/components/theme.py").read_text(encoding="utf-8")

    for label in ("首页", "智能问答", "知识库", "Text2SQL", "SQL 审查", "数仓设计"):
        assert label in app_content

    assert "st.Page" in app_content
    assert "st.navigation" in app_content
    assert "@keyframes fadeUp" in theme_content
    assert "prefers-reduced-motion" in theme_content
    assert "datacopilot-hero" in theme_content
    assert "dc-action-grid" in theme_content
    assert "最近操作" in app_content


def test_hero_layout_keeps_text_visible() -> None:
    theme_content = Path("frontend/components/theme.py").read_text(encoding="utf-8")

    assert "overflow: visible;" in theme_content
    assert "min-height: 8.5rem;" in theme_content
    assert "box-sizing: border-box;" in theme_content
    assert "position: relative;" in theme_content
    assert "z-index: 1;" in theme_content


def test_streamlit_pages_define_unique_url_paths() -> None:
    app_content = Path("frontend/app.py").read_text(encoding="utf-8")
    expected_paths = [
        'url_path="chat"',
        'url_path="knowledge"',
        'url_path="text2sql"',
        'url_path="sql-review"',
        'url_path="warehouse-design"',
    ]

    for path in expected_paths:
        assert path in app_content
    assert 'default=True' in app_content
    assert 'url_path="home"' not in app_content
    assert '"pages/warehouse_design.py"' in app_content
    assert "from frontend.pages import" not in app_content
    assert app_content.count("url_path=") == len(expected_paths)


def test_frontend_pages_use_chinese_display_text() -> None:
    expected_text = {
        "frontend/app.py": ["数据工程 AI 工作台", "系统状态", "工作区"],
        "frontend/pages/chat.py": ["智能问答", "上下文设置", "向 DataPilot-AI 提问", "演示问题"],
        "frontend/pages/knowledge_base.py": ["知识库", "上传文档", "文档列表", "知识问答"],
        "frontend/pages/text2sql.py": ["自然语言生成 SQL", "业务需求", "生成 SQL", "一键示例"],
        "frontend/pages/sql_review.py": ["SQL 审查", "风险等级", "优化建议", "一键示例"],
        "frontend/pages/warehouse_design.py": ["数仓设计", "业务需求", "生成数仓方案", "一键示例"],
    }

    for file_name, labels in expected_text.items():
        content = Path(file_name).read_text(encoding="utf-8")
        for label in labels:
            assert label in content


def test_frontend_pages_include_guided_demo_prompts() -> None:
    expected_text = {
        "frontend/app.py": [
            "什么是 Spark AQE？",
            "统计最近7天活跃用户并检查 SQL",
            "设计电商订单分析数仓",
        ],
        "frontend/pages/chat.py": [
            "什么是 Spark AQE？",
            "统计最近7天活跃用户并检查 SQL",
            "请帮我设计电商订单分析数仓",
        ],
        "frontend/pages/text2sql.py": ["活跃用户分析", "订单 GMV 看板"],
        "frontend/pages/sql_review.py": ["Spark 分区缺失", "SELECT 星号风险"],
        "frontend/pages/warehouse_design.py": ["电商订单主题", "用户行为主题"],
    }

    for file_name, labels in expected_text.items():
        content = Path(file_name).read_text(encoding="utf-8")
        for label in labels:
            assert label in content
