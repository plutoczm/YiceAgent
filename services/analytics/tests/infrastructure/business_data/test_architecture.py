import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIRECTORY = (
    REPOSITORY_ROOT / "backend" / "app" / "infrastructure" / "business_data"
)
FORBIDDEN_IMPORT_PREFIXES = (
    "backend.app.presentation",
    "backend.app.infrastructure.llm",
    "backend.app.application.agent",
    "backend.app.application.sql_review",
    "backend.app.application.text2sql.text2sql_service",
    "backend.app.application.text2sql.prompt_builder",
    "backend.app.application.text2sql.sql_validator",
    "backend.app.domain.ports.llm_provider",
    "frontend",
    "supportops",
    "langchain",
    "langgraph",
    "streamlit",
)


def test_contract_adapter_does_not_depend_on_agents_llms_apis_or_supportops() -> None:
    for source_path in ADAPTER_DIRECTORY.glob("*.py"):
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = [node.module]
            else:
                continue
            assert not any(
                imported == forbidden
                or imported.startswith(f"{forbidden}.")
                for imported in imported_modules
                for forbidden in FORBIDDEN_IMPORT_PREFIXES
            ), f"{source_path.name} has a forbidden import: {imported_modules}"


def test_contract_adapter_is_not_wired_to_production_entrypoints() -> None:
    production_roots = (
        REPOSITORY_ROOT / "backend" / "app" / "presentation",
        REPOSITORY_ROOT / "backend" / "app" / "application" / "agent",
        REPOSITORY_ROOT / "frontend",
    )
    adapter_names = {"BusinessDataContractCatalog", "from_repository_root"}

    for root in production_roots:
        for source_path in root.rglob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(
                        "backend.app.infrastructure.business_data"
                    ), f"{source_path} wires the contract adapter"
                if isinstance(node, ast.Name):
                    assert node.id not in adapter_names, (
                        f"{source_path} wires the contract adapter"
                    )
