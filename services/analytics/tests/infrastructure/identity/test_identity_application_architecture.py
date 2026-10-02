import ast
from pathlib import Path


APPLICATION_ANALYTICS = (
    Path(__file__).resolve().parents[3]
    / "backend/app/application/business_analytics"
)


def test_business_analytics_application_does_not_import_http_agent_or_infrastructure() -> None:
    forbidden_roots = {
        "fastapi",
        "langgraph",
        "langchain_core",
        "duckdb",
        "jwt",
    }
    for path in APPLICATION_ANALYTICS.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            else:
                continue
            assert not any(
                item.split(".")[0] in forbidden_roots
                or item.startswith("backend.app.infrastructure")
                or item.startswith("backend.app.application.agent")
                for item in imported
            ), path
