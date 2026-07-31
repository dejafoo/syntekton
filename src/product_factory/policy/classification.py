"""Ingress classification guard — fail-closed for known secret material (PM0.B)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from product_factory.domain.errors import UnsafeOperationError
from product_factory.validation.pipeline import SECRET_PATTERNS

ClassificationLabel = Literal[
    "public",
    "internal",
    "confidential",
    "secret",
    "regulated",
]

RULE_VERSION = "classification.v1"

# Extra fail-closed patterns beyond SECRET_PATTERNS (credentials / private keys).
_EXTRA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_secret_access_key", re.compile(r"(?i)aws.?secret.?access.?key\s*[:=]\s*\S{20,}")),
    ("generic_bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*")),
    ("github_pat", re.compile(r"ghp_[A-Za-z0-9]{36}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
]


@dataclass(frozen=True)
class ClassificationDecision:
    label: ClassificationLabel
    outcome: Literal["allow", "redact", "block"]
    rule_version: str
    matched_rules: tuple[str, ...]
    redacted: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "outcome": self.outcome,
            "rule_version": self.rule_version,
            "matched_rules": list(self.matched_rules),
            "redacted": self.redacted,
        }


def classify_text(text: str, *, fail_closed: bool = True) -> ClassificationDecision:
    """Classify content; block known secret material when fail_closed."""
    matched: list[str] = []
    for idx, pat in enumerate(SECRET_PATTERNS):
        if pat.search(text or ""):
            matched.append(f"secret_patterns[{idx}]")
    for name, pat in _EXTRA_PATTERNS:
        if pat.search(text or ""):
            matched.append(name)
    if matched:
        return ClassificationDecision(
            label="secret",
            outcome="block" if fail_closed else "redact",
            rule_version=RULE_VERSION,
            matched_rules=tuple(matched),
            redacted=not fail_closed,
        )
    return ClassificationDecision(
        label="internal",
        outcome="allow",
        rule_version=RULE_VERSION,
        matched_rules=(),
    )


def assert_ingress_allowed(text: str, *, source: str = "ingress") -> ClassificationDecision:
    """Raise if text contains prohibited secret material."""
    decision = classify_text(text, fail_closed=True)
    if decision.outcome == "block":
        raise UnsafeOperationError(
            f"Ingress blocked by classification ({source})",
            details=decision.as_payload(),
        )
    return decision


def classify_payload(payload: Any, *, fail_closed: bool = True) -> ClassificationDecision:
    if isinstance(payload, str):
        return classify_text(payload, fail_closed=fail_closed)
    if isinstance(payload, (dict, list)):
        import json

        return classify_text(
            json.dumps(payload, default=str),
            fail_closed=fail_closed,
        )
    return classify_text(str(payload), fail_closed=fail_closed)
