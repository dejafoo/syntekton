"""Tool registry."""

from __future__ import annotations

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.tools import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise ToolAuthorizationError(f"Unregistered tool: {name}")
        return self._tools[name]

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def names(self) -> set[str]:
        return set(self._tools)


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    specs = [
        ToolDefinition(
            name="list_files",
            description="List files under a directory resource",
            tool_class="repository_read",
            input_schema={
                "type": "object",
                "properties": {
                    "directory": {"type": "string"},
                    "glob": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["directory"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="read_file",
            description="Read a file with optional line range",
            tool_class="repository_read",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "max_bytes": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="search_text",
            description="Search text in the repository",
            tool_class="repository_read",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path_filter": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="git_diff",
            description="Produce a patch for the worktree",
            tool_class="git_read",
            input_schema={
                "type": "object",
                "properties": {"base_ref": {"type": "string"}},
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="git_status",
            description="Structured git status for the worktree",
            tool_class="git_read",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            risk_class="R0",
        ),
        ToolDefinition(
            name="apply_patch",
            description="Apply a unified patch in the worktree",
            tool_class="repository_write",
            input_schema={
                "type": "object",
                "properties": {"patch": {"type": "string"}},
                "required": ["patch"],
                "additionalProperties": False,
            },
            risk_class="R2",
        ),
        ToolDefinition(
            name="create_file",
            description="Create a new file in the worktree",
            tool_class="repository_write",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            risk_class="R2",
        ),
        ToolDefinition(
            name="write_artifact",
            description="Write content to the artifact store / run output",
            tool_class="artifact_write",
            input_schema={
                "type": "object",
                "properties": {
                    "logical_name": {"type": "string"},
                    "content": {"type": "string"},
                    "media_type": {"type": "string"},
                },
                "required": ["logical_name", "content"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="extract_document",
            description="Extract bounded text from a persisted source capture",
            tool_class="evidence_build",
            input_schema={
                "type": "object",
                "properties": {
                    "source_sha256": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 1},
                    "section": {"type": "string"},
                },
                "required": ["source_sha256", "max_chars"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="normalize_citation",
            description="Create a deterministic policy-checked source record",
            tool_class="evidence_build",
            input_schema={
                "type": "object",
                "properties": {
                    "source_sha256": {"type": "string"},
                    "source_class": {"type": "string"},
                    "published_at": {"type": "string"},
                },
                "required": ["source_sha256", "source_class"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="compare_options",
            description="Write an explicit-unknown option matrix artifact",
            tool_class="evidence_build",
            input_schema={
                "type": "object",
                "properties": {
                    "options": {"type": "array"},
                    "criteria": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                },
                "required": ["options", "criteria", "evidence_refs"],
                "additionalProperties": False,
            },
            risk_class="R1",
        ),
        ToolDefinition(
            name="run_validation_command",
            description=(
                "Run a registered validation command by policy id "
                "(e.g. 'python_tests'). Pass only a registered command_id — "
                "never a validator label like 'behavioral:python_tests', never "
                "a raw executable like 'pytest'."
            ),
            tool_class="validation_command",
            input_schema={
                "type": "object",
                "properties": {
                    "command_id": {
                        "type": "string",
                        "description": (
                            "Registered policy command id from policies.yaml "
                            "(e.g. python_tests, python_typecheck)."
                        ),
                    }
                },
                "required": ["command_id"],
                "additionalProperties": False,
            },
            risk_class="R3",
        ),
    ]
    for tool in specs:
        registry.register(tool)
    return registry
