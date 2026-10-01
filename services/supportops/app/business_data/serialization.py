from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.business_data.models import DeliveryValidationError, ExportValidationError

DECIMAL_PATTERN = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")


def canonical_decimal(value: float | int | Decimal) -> str:
    """Convert a finite persistence number to the contract's decimal string."""

    if isinstance(value, bool):
        raise ExportValidationError("invalid_decimal_value")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExportValidationError("invalid_decimal_value") from exc
    if not decimal_value.is_finite():
        raise ExportValidationError("non_finite_decimal_value")
    if decimal_value.is_zero():
        return "0"

    rendered = format(decimal_value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered == "-0" else rendered


def canonical_timestamp(value: datetime) -> str:
    """Render a persistence timestamp as an RFC 3339 UTC instant."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    timespec = "microseconds" if value.microsecond else "seconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def canonical_json_line_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_canonical_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DeliveryValidationError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DeliveryValidationError("invalid_timestamp") from exc
    if canonical_timestamp(parsed) != value:
        raise DeliveryValidationError("noncanonical_timestamp")
