import logging
import traceback
from contextvars import ContextVar, Token
from types import TracebackType


REQUEST_ID_CONTEXT_KEY = "request_id"
TRACE_ID_CONTEXT_KEY = "trace_id"

_request_id: ContextVar[str | None] = ContextVar(REQUEST_ID_CONTEXT_KEY, default=None)
_trace_id: ContextVar[str | None] = ContextVar(TRACE_ID_CONTEXT_KEY, default=None)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        record.trace_id = get_trace_id()
        return True


def get_logger(name: str = "datacopilot") -> logging.Logger:
    return logging.getLogger(name)


def get_request_id() -> str | None:
    return _request_id.get()


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_request_context(
    request_id: str | None = None,
    trace_id: str | None = None,
) -> tuple[Token[str | None], Token[str | None]]:
    request_token = _request_id.set(request_id)
    trace_token = _trace_id.set(trace_id)
    return request_token, trace_token


def reset_request_context(
    tokens: tuple[Token[str | None], Token[str | None]],
) -> None:
    request_token, trace_token = tokens
    _request_id.reset(request_token)
    _trace_id.reset(trace_token)


def clear_request_context() -> None:
    _request_id.set(None)
    _trace_id.set(None)


def log_exception(
    logger: logging.Logger,
    message: str,
    exc: BaseException,
    *,
    level: int = logging.ERROR,
) -> None:
    exc_type = type(exc)
    exc_traceback: TracebackType | None = exc.__traceback__
    logger.log(
        level,
        message,
        extra={
            "exception": {
                "type": exc_type.__name__,
                "message": str(exc),
                "traceback": "".join(
                    traceback.format_exception(exc_type, exc, exc_traceback)
                ),
            }
        },
    )
