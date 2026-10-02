from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HOST_PROJECT_PATH = str(PROJECT_ROOT)


def load_compose() -> dict:
    compose_file = Path("docker-compose.yml")
    assert compose_file.is_file()
    return yaml.safe_load(compose_file.read_text(encoding="utf-8"))


def test_compose_defines_required_services_profiles_and_network() -> None:
    compose = load_compose()
    services = compose["services"]

    assert {"backend", "frontend", "chromadb", "redis", "ollama"}.issubset(services)
    assert services["frontend"]["depends_on"]["backend"]["condition"] == "service_healthy"
    assert services["backend"]["depends_on"]["chromadb"]["condition"] == "service_healthy"
    assert services["backend"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert services["ollama"]["profiles"] == ["ollama"]
    assert "datacopilot" in compose["networks"]


def test_compose_backend_exposes_routing_and_local_model_env_passthrough() -> None:
    compose = load_compose()
    backend_env = compose["x-backend-env"]

    assert backend_env["DATACOPILOT_LLM__ROUTING_ENABLED"] == "${ROUTING_ENABLED:-false}"
    assert backend_env["DATACOPILOT_LOCAL__ENABLED"] == "${LOCAL_MODEL_ENABLED:-false}"
    assert backend_env["DATACOPILOT_LOCAL__BASE_URL"].startswith("${LOCAL_MODEL_BASE_URL")
    assert backend_env["DATACOPILOT_LOCAL__CHAT_MODEL"].startswith("${LOCAL_MODEL_NAME")
    assert backend_env["DATACOPILOT_VECTOR_STORE__MODE"] == "http"
    assert backend_env["DATACOPILOT_VECTOR_STORE__HOST"] == "chromadb"
    assert backend_env["DATACOPILOT_VECTOR_STORE__PORT"] == "8000"
    assert backend_env["DATACOPILOT_MEMORY__BACKEND"] == "redis"
    assert backend_env["DATACOPILOT_MEMORY__REDIS_URL"] == "redis://redis:6379/0"
    assert backend_env["DATACOPILOT_EMBEDDINGS__DEFAULT_MODEL"] == "BAAI/bge-m3"


def test_compose_uses_only_project_local_bind_mounts_and_no_anonymous_volumes() -> None:
    compose = load_compose()

    for service_name, service in compose["services"].items():
        for volume in service.get("volumes", []):
            assert isinstance(volume, dict), f"{service_name} must use long volume syntax"
            assert volume["type"] == "bind", f"{service_name} must not use anonymous volumes"
            source = volume["source"]
            assert not Path(source).is_absolute(), f"{service_name} source must be relative"
            assert source.startswith(("./data", "./models")), (
                f"{service_name} volume must stay under data/ or models/"
            )


def test_compose_does_not_embed_host_project_path() -> None:
    content = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert HOST_PROJECT_PATH not in content


def test_compose_resource_limits_match_shared_server_safety_budget() -> None:
    compose = load_compose()
    services = compose["services"]

    assert services["backend"]["deploy"]["resources"]["limits"] == {
        "cpus": "4.0",
        "memory": "8G",
    }
    assert services["frontend"]["deploy"]["resources"]["limits"] == {
        "cpus": "1.0",
        "memory": "2G",
    }
    assert services["chromadb"]["deploy"]["resources"]["limits"] == {
        "cpus": "2.0",
        "memory": "4G",
    }


def test_compose_has_healthchecks_and_restart_policies() -> None:
    compose = load_compose()
    services = compose["services"]
    for service_name in ("backend", "frontend", "chromadb", "redis"):
        service = services[service_name]
        assert service["restart"] == "unless-stopped"
        assert "healthcheck" in service
        expected_interval = "10s" if service_name == "redis" else "30s"
        assert service["healthcheck"]["interval"] == expected_interval
        assert service["healthcheck"]["retries"] >= 3
    assert "/health/live" in " ".join(services["backend"]["healthcheck"]["test"])
    assert services["redis"]["command"] == [
        "redis-server",
        "--appendonly",
        "yes",
        "--appendfsync",
        "everysec",
    ]


def test_production_env_file_sets_safe_runtime_defaults_without_secrets() -> None:
    env_file = Path("docker/.env.production")

    assert env_file.is_file()
    content = env_file.read_text(encoding="utf-8")
    assert "DATACOPILOT_ENVIRONMENT=production" in content
    assert "DATACOPILOT_DEBUG=false" in content
    assert "DATACOPILOT_UID=1014" in content
    assert "DATACOPILOT_GID=1015" in content
    assert "BACKEND_PORT=8000" in content
    assert "CHROMADB_PORT=8001" in content
    assert "FRONTEND_PORT=8501" in content
    assert "OLLAMA_PORT=11434" in content
    assert "DATACOPILOT_PATHS__PROJECT_ROOT=/app" in content
    assert "DATACOPILOT_PATHS__DATA_DIR=data" in content
    assert "DATACOPILOT_PATHS__MODELS_DIR=models" in content
    assert "BACKEND_URL=http://backend:8000" in content
    assert "DEEPSEEK_API_KEY=" in content
    assert "sk-" not in content.lower()
    assert HOST_PROJECT_PATH not in content


def test_env_example_does_not_set_legacy_chroma_client_variables() -> None:
    content = Path(".env.example").read_text(encoding="utf-8")

    assert "CHROMA_DB_IMPL" not in content
    assert "CHROMA_PERSIST_DIRECTORY" not in content
    assert "sk-" not in content.lower()


def test_backend_dockerfile_is_production_optimized() -> None:
    dockerfile = Path("backend/Dockerfile")

    assert dockerfile.is_file()
    content = dockerfile.read_text(encoding="utf-8")
    assert "python:3.12-slim" in content
    assert "DATACOPILOT_UID=1014" in content
    assert "DATACOPILOT_GID=1015" in content
    assert "useradd" in content
    assert "pip install --no-cache-dir" in content
    assert "download.pytorch.org/whl/cpu" in content
    assert "PYTORCH_VERSION=2.8.0" in content
    assert "backend/requirements.txt" in content
    assert "HEALTHCHECK" in content
    assert "uvicorn" in content
    assert "--host=0.0.0.0" in content
    assert "EXPOSE 8000" in content


def test_frontend_dockerfile_is_production_optimized() -> None:
    dockerfile = Path("frontend/Dockerfile")

    assert dockerfile.is_file()
    content = dockerfile.read_text(encoding="utf-8")
    assert "python:3.12-slim" in content
    assert "DATACOPILOT_UID=1014" in content
    assert "DATACOPILOT_GID=1015" in content
    assert "useradd" in content
    assert "pip install --no-cache-dir" in content
    assert "HEALTHCHECK" in content
    assert "streamlit" in content
    assert "EXPOSE 8501" in content
