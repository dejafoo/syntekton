"""Schema kind taxonomy for the PM0 registry."""

from __future__ import annotations

from typing import Literal

SchemaKind = Literal[
    "handoff",
    "task_output",
    "source_record",
    "tool_receipt",
    "skill_io",
    "profile",
    "reserved",
]

HANDOFF_STATE = Literal["draft", "evidence_complete", "approved", "superseded"]
