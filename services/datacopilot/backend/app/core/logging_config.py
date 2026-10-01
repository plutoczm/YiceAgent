import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.core.logger import (
    REQUEST_ID_CONTEXT_KEY,
    TRACE_ID_CONTEXT_KEY,
    RequestContextFilter,
    log_exception,
    reset_request_context,
    set_request_context,
)
from backend.app.core.settings import Settings


LOG_RECORD_BUILTIN_FIELDS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

ASGIApp = Callable[
    [
        MutableMapping[str, Any],
        Callable[[], Awaitable[MutableMapping[str, Any]]],
        Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]


class JsonLogFormatter(logging.Formatter):
    def __init__(self, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "service": self.service_name,
            "environment": self.environment,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "request_id": getattr(record, "request_id", None),
            "trace_id": getattr(record, "trace_id", None),
        }

        exception_payload = getattr(record, "exception", None)
        if exception_payload is not None:
            payload["exception"] = exception_payload
        elif record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key not in LOG_RECORD_BUILTIN_FIELDS and key not in payload:
                payload[key] = value

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging(settings: Settings) -> logging.Logger:
    logger = logging.getLogger("datacopilot")
    logger.handlers.clear()
    logger.filters.clear()
    logger.setLevel(logging.getLevelName(settings.logging.level))
    logger.propagate = False

    request_filter = RequestContextFilter()
    formatter: logging.Formatter
    if settings.logging.json_enabled:
        formatter = JsonLogFormatter(
            service_name=settings.app.name,
            environment=settings.environment.value,
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] "
            "request_id=%(request_id)s trace_id=%(trace_id)s %(message)s"
        )

    if settings.logging.console_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(request_filter)
        logger.addHandler(console_handler)

    if settings.logging.file_enabled:
        file_path = Path(settings.logging.file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=file_path,
            maxBytes=settings.logging.max_bytes,
            backupCount=settings.logging.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        logger.addHandler(file_handler)

    return logger


class RequestTraceMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        request_id_header: str = "x-request-id",
        trace_id_header: str = "x-trace-id",
        logger: logging.Logger | None = None,
    ) -> None:
        self.app = app
        self.request_id_header = request_id_header.lower()
        self.trace_id_header = trace_id_header.lower()
        self.logger = logger or logging.getLogger("datacopilot.request")

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = _decode_headers(scope.get("headers", []))
        request_id = headers.get(self.request_id_header) or str(uuid4())
        trace_id = headers.get(self.trace_id_header) or request_id
        state = scope.setdefault("state", {})
        state[REQUEST_ID_CONTEXT_KEY] = request_id
        state[TRACE_ID_CONTEXT_KEY] = trace_id
        tokens = set_request_context(request_id=request_id, trace_id=trace_id)
        started_at = time.perf_counter()
        status_code = 500

        async def send_with_trace(message: MutableMapping[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                message_headers = list(message.get("headers", []))
                _set_header(message_headers, self.request_id_header, request_id)
                _set_header(message_headers, self.trace_id_header, trace_id)
                message["headers"] = message_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        except Exception as exc:
            log_exception(self.logger, "Unhandled request exception", exc)
            raise
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 3)
            self.logger.info(
                "HTTP request completed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            reset_request_context(tokens)


def setup_fastapi_logging(app: Any, settings: Settings) -> logging.Logger:
    logger = configure_logging(settings)
    app.add_middleware(
        RequestTraceMiddleware,
        request_id_header=settings.logging.request_id_header,
        trace_id_header=settings.logging.trace_id_header,
        logger=logger,
    )
    return logger


def _decode_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in raw_headers
    }


def _set_header(headers: list[tuple[bytes, bytes]], name: str, value: str) -> None:
    encoded_name = name.lower().encode("latin-1")
    encoded_value = value.encode("latin-1")
    for index, (header_name, _) in enumerate(headers):
        if header_name.lower() == encoded_name:
            headers[index] = (encoded_name, encoded_value)
            return
    headers.append((encoded_name, encoded_value))
