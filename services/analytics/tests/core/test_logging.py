import asyncio
import json
import logging
from pathlib import Path

import pytest

from backend.app.core.logging_config import (
    JsonLogFormatter,
    RequestTraceMiddleware,
    configure_logging,
)
from backend.app.core.logger import (
    clear_request_context,
    get_logger,
    get_request_id,
    log_exception,
    set_request_context,
)
from backend.app.core.settings import LoggingSettings, Settings


def test_logging_settings_defaults_are_project_local() -> None:
    settings = Settings(_env_file=None)

    assert settings.logging.level == "INFO"
    assert settings.logging.console_enabled is True
    assert settings.logging.file_enabled is True
    assert settings.logging.file_path == settings.paths.logs_dir / "datacopilot.log"
    assert settings.logging.max_bytes == 20 * 1024 * 1024
    assert settings.logging.backup_count == 10


def test_logging_settings_reject_invalid_level() -> None:
    with pytest.raises(ValueError, match="level must be one of"):
        LoggingSettings(level="TRACE")


def test_json_formatter_outputs_structured_record() -> None:
    formatter = JsonLogFormatter(service_name="DataPilot-AI", environment="test")
    record = logging.LogRecord(
        name="datacopilot.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.trace_id = "trace-abc"

    payload = json.loads(formatter.format(record))

    assert payload["service"] == "DataPilot-AI"
    assert payload["environment"] == "test"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "datacopilot.test"
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-123"
    assert payload["trace_id"] == "trace-abc"


def test_configure_logging_adds_console_and_rotating_file_handlers(
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "logs" / "app.log"
    settings = Settings(
        _env_file=None,
        environment="test",
        logging={
            "level": "DEBUG",
            "file_path": log_file,
            "max_bytes": 1024,
            "backup_count": 3,
        },
    )

    logger = configure_logging(settings)

    assert logger.level == logging.DEBUG
    assert len(logger.handlers) == 2

    file_handlers = [
        handler
        for handler in logger.handlers
        if handler.__class__.__name__ == "RotatingFileHandler"
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].baseFilename == str(log_file)
    assert file_handlers[0].maxBytes == 1024
    assert file_handlers[0].backupCount == 3

    logger.info("structured file log", extra={"component": "unit-test"})

    first_line = log_file.read_text(encoding="utf-8").splitlines()[0]
    payload = json.loads(first_line)

    assert payload["message"] == "structured file log"
    assert payload["component"] == "unit-test"
    assert payload["level"] == "INFO"


def test_request_context_is_attached_to_logs(tmp_path: Path) -> None:
    log_file = tmp_path / "request.log"
    settings = Settings(
        _env_file=None,
        environment="test",
        logging={
            "console_enabled": False,
            "file_enabled": True,
            "file_path": log_file,
        },
    )
    configure_logging(settings)
    set_request_context(request_id="req-789", trace_id="trace-789")

    get_logger("datacopilot.request").warning("with context")
    clear_request_context()

    payload = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])

    assert payload["request_id"] == "req-789"
    assert payload["trace_id"] == "trace-789"


def test_log_exception_records_exception_payload(tmp_path: Path) -> None:
    log_file = tmp_path / "exception.log"
    settings = Settings(
        _env_file=None,
        environment="test",
        logging={
            "console_enabled": False,
            "file_enabled": True,
            "file_path": log_file,
        },
    )
    configure_logging(settings)

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        log_exception(get_logger("datacopilot.exception"), "handled failure", exc)

    payload = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])

    assert payload["level"] == "ERROR"
    assert payload["message"] == "handled failure"
    assert payload["exception"]["type"] == "RuntimeError"
    assert payload["exception"]["message"] == "boom"
    assert "Traceback" in payload["exception"]["traceback"]


def test_request_trace_middleware_sets_and_clears_request_context() -> None:
    observed_request_id: str | None = None
    response_headers: list[tuple[bytes, bytes]] = []

    async def app(scope, receive, send):
        nonlocal observed_request_id
        observed_request_id = get_request_id()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            response_headers.extend(message["headers"])

    middleware = RequestTraceMiddleware(app)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(b"x-request-id", b"req-from-client")],
    }

    asyncio.run(middleware(scope, receive, send))

    assert observed_request_id == "req-from-client"
    assert (b"x-request-id", b"req-from-client") in response_headers
    assert get_request_id() is None
