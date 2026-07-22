"""Logging helpers with secret redaction."""

from __future__ import annotations

import logging
import re

_REDACT_PATTERNS = [
    re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE),
    re.compile(r"(OPENROUTER_API_KEY\s*=\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)[^\"'\s]+", re.IGNORECASE),
]


def redact(text: str) -> str:
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub(r"\1***", out)
    return out


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger("product_factory")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    root.setLevel(level)
