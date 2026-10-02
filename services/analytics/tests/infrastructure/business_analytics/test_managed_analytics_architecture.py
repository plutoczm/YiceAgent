import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_ROOT = (
    REPOSITORY_ROOT / "backend" / "app" / "application" / "business_analytics"
)
PRODUCTION_ROOTS = (
    REPOSITORY_ROOT / "backend" / "app" / "presentation",
    REPOSITORY_ROOT / "backend" / "app" / "application" / "agent",
    REPOSITORY_ROOT / "frontend",
)
FORBIDDEN_APPLICATION_IMPORTS = (
    "backend.app.application.agent",
    "backend.app.infrastructure",
    "backend.app.presentation",
    "backend.app.domain.ports.llm_provider",
    "backend.app.application.text2sql.text2sql_service",
    "deepseek",
    "duckdb",
    "ollama",
    "openai",
    "supportops",
    "langchain",
    "langgraph",
    "streamlit",
    "frontend",
)


def test_managed_application_uses_ports_not_runtime_or_text2sql_service() -> None:
    for source_path in APPLICATION_ROOT.glob("*.py"):
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            else:
                continue
            assert not any(
                imported == forbidden or imported.startswith(f"{forbidden}.")
                for imported in imported_modules
                for forbidden in FORBIDDEN_APPLICATION_IMPORTS
            ), f"{source_path.name} imports a forbidden dependency: {imported_modules}"


def test_managed_pipeline_is_only_wired_through_explicit_d5_entrypoints() -> None:
    forbidden_prefixes = (
        "backend.app.application.business_analytics",
        "backend.app.infrastructure.business_analytics",
    )
    allowed_d5_entrypoints = {
        REPOSITORY_ROOT
        / "backend/app/presentation/api/dependencies/business_analytics.py",
        REPOSITORY_ROOT
        / "backend/app/presentation/api/routes/business_analytics.py",
        REPOSITORY_ROOT
        / "backend/app/presentation/api/schemas/business_analytics.py",
        REPOSITORY_ROOT / "backend/app/application/agent/business_analytics_adapter.py",
        REPOSITORY_ROOT / "backend/app/application/agent/graph.py",
        REPOSITORY_ROOT / "backend/app/application/agent/nodes.py",
        REPOSITORY_ROOT / "backend/app/application/agent/tools.py",
    }
    for root in PRODUCTION_ROOTS:
        if not root.exists():
            continue
        for source_path in root.rglob("*.py"):
            if source_path in allowed_d5_entrypoints:
                continue
            module = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(module):
                if isinstance(node, ast.Import):
                    imported_modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules = [node.module]
                else:
                    continue
                assert not any(
                    imported == forbidden or imported.startswith(f"{forbidden}.")
                    for imported in imported_modules
                    for forbidden in forbidden_prefixes
                ), f"{source_path} wires the managed pipeline: {imported_modules}"
