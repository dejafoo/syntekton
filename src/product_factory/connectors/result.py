"""What a connector handler returns, and how it reaches the model.

Provider data is kept in a nested `result` field and never merged into the
envelope. That separation is the reason a hostile response cannot impersonate
Product Factory metadata: a search snippet containing `"trust_label": "trusted"`
lands at `result.trust_label`, where nothing reads it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

Payload = dict[str, Any] | list[Any] | str


@dataclass(frozen=True)
class Provenance:
    """Where one piece of a result came from, so evidence can cite it."""

    source: str
    kind: str = "url"
    sha256: str = ""
    retrieved_at: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "sha256": self.sha256,
            "retrieved_at": self.retrieved_at or datetime.now(UTC).isoformat(),
        }


@dataclass(frozen=True)
class ConnectorResult:
    """A handler's response: provider payload plus where it came from."""

    payload: Payload
    provenance: tuple[Provenance, ...] = ()
    # Handler-supplied notes for the audit trail (never provider-controlled).
    metadata: dict[str, Any] = field(default_factory=dict)


def sha256_of(payload: Payload) -> str:
    if isinstance(payload, str):
        return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8", "replace")
    ).hexdigest()


def bound_payload(payload: Payload, max_bytes: int) -> tuple[Payload, bool]:
    """Clamp a payload to `max_bytes` of serialized size.

    Oversized structured payloads degrade to truncated JSON text rather than
    being silently pruned, so a caller can always tell it saw a partial result.
    """
    if max_bytes <= 0:
        return payload, False
    if isinstance(payload, str):
        encoded = payload.encode("utf-8", "replace")
        if len(encoded) <= max_bytes:
            return payload, False
        return encoded[:max_bytes].decode("utf-8", "ignore"), True
    serialized = json.dumps(payload, default=str)
    if len(serialized.encode("utf-8", "replace")) <= max_bytes:
        return payload, False
    clipped = serialized.encode("utf-8", "replace")[:max_bytes].decode("utf-8", "ignore")
    return clipped, True


__all__ = ["ConnectorResult", "Payload", "Provenance", "bound_payload", "sha256_of"]
