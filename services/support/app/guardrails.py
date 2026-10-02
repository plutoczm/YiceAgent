from __future__ import annotations

import re

from app.domain import GuardrailAssessment


class InputGuardrails:
    """Cheap deterministic guardrails that run before any model or tool call."""

    _injection_patterns = (
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
        re.compile(r"reveal\s+(the\s+)?system\s+prompt", re.IGNORECASE),
        re.compile(r"developer\s+message", re.IGNORECASE),
        re.compile(
            r"(?:忽略|无视|不要遵守)(?:之前|以上|前面|上面)(?:的)?.{0,8}"
            r"(?:指令|规则|要求|限制)"
        ),
        re.compile(r"(泄露|输出|显示).{0,8}(系统提示词|system prompt)", re.IGNORECASE),
        re.compile(r"绕过.{0,8}(安全|规则|限制)"),
    )
    _email = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
    _phone = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
    _card = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")

    def inspect(self, text: str) -> GuardrailAssessment:
        labels: list[str] = []
        if any(pattern.search(text) for pattern in self._injection_patterns):
            labels.append("prompt_injection")

        sanitized, redactions = self._redact_pii(text)
        if redactions:
            labels.append("pii_redacted")

        return GuardrailAssessment(
            blocked="prompt_injection" in labels,
            sanitized_text=sanitized,
            labels=labels,
            redactions=redactions,
        )

    def _redact_pii(self, text: str) -> tuple[str, list[str]]:
        redactions: list[str] = []

        def replace(pattern: re.Pattern[str], value: str, label: str) -> str:
            nonlocal text
            if pattern.search(text):
                redactions.append(label)
                text = pattern.sub(value, text)
            return text

        replace(self._email, "[REDACTED_EMAIL]", "email")
        replace(self._phone, "[REDACTED_PHONE]", "phone")
        replace(self._card, "[REDACTED_CARD]", "payment_card")
        return text, redactions
