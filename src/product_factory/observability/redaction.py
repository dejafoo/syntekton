"""Capture levels and recursive redaction for observability payloads."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from product_factory.observability.contracts import CaptureLevel, ContentRef
from product_factory.observability.logging import redact as redact_text

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "token",
        "password",
        "secret",
        "access_key",
        "private_key",
        "credential",
        "openrouter_api_key",
        "bearer",
    }
)

_SECRET_VALUE = re.compile(
    r"(?i)(sk-[a-z0-9\-_]{10,}|bearer\s+[a-z0-9\-_\.]{10,}|api[_-]?key\s*[:=]\s*\S+)"
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _SENSITIVE_KEYS:
        return True
    return any(part in lowered for part in ("password", "secret", "token", "api_key"))


def redact_value(value: Any, *, max_string: int = 4_000) -> Any:
    """Recursively redact secrets and truncate large strings."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(str(k)):
                out[str(k)] = "***"
            else:
                out[str(k)] = redact_value(v, max_string=max_string)
        return out
    if isinstance(value, list):
        return [redact_value(v, max_string=max_string) for v in value[:200]]
    if isinstance(value, str):
        text = redact_text(value)
        text = _SECRET_VALUE.sub("***", text)
        if len(text) > max_string:
            return text[:max_string] + f"...<truncated:{len(value)}>"
        return text
    return value


def content_hash(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(body).hexdigest()


def capture_content(
    payload: Any,
    *,
    level: CaptureLevel,
    media_type: str = "application/json",
    logical_name: str | None = None,
    preview_chars: int = 240,
) -> tuple[ContentRef | None, Any | None]:
    """
    Apply capture policy.

    Returns (content_ref, body_to_persist_or_None).
    For METADATA/OFF, body is None and only a hash/ref is produced when possible.
    """
    if level == CaptureLevel.OFF:
        return None, None

    digest = content_hash(payload)
    preview: str | None = None
    body: Any | None = None

    if level == CaptureLevel.METADATA:
        preview = None
        body = None
    elif level == CaptureLevel.REDACTED:
        body = redact_value(payload)
        preview_src = json.dumps(body, default=str) if not isinstance(body, str) else body
        preview = preview_src[:preview_chars]
    else:  # FULL
        body = payload
        preview_src = json.dumps(body, default=str) if not isinstance(body, str) else str(body)
        preview = redact_text(preview_src[:preview_chars])

    byte_count = len(json.dumps(payload, default=str).encode())
    ref = ContentRef(
        sha256=digest,
        media_type=media_type,
        byte_count=byte_count,
        capture_level=level,
        preview=preview,
        logical_name=logical_name,
    )
    return ref, body
