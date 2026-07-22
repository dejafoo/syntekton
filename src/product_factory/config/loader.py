"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError


class ModelProfileConfig(BaseModel):
    provider_adapter: str = "openrouter"
    model: str
    capabilities: list[str] = Field(default_factory=list)
    structured_outputs: bool = True
    tool_calling: bool = True
    context_soft_limit: int = 32_000
    max_output_tokens: int = 8_000
    temperature: float = 0.1
    reasoning_effort: str | None = None
    provider: dict[str, Any] = Field(default_factory=dict)
    pricing: dict[str, str] = Field(default_factory=dict)
    run_policy: dict[str, Any] = Field(default_factory=dict)


class ModelsConfig(BaseModel):
    profiles: dict[str, ModelProfileConfig]


class PoliciesConfig(BaseModel):
    allow_dirty_repo: bool = False
    max_artifact_bytes: int = 5_000_000
    prohibited_path_globs: list[str] = Field(
        default_factory=lambda: [".env", "**/.env", "**/secrets/**"]
    )
    registered_commands: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WorkflowsConfig(BaseModel):
    default_workflow: str = "code_change"
    workflows: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AppConfig(BaseModel):
    root: Path
    models: ModelsConfig
    policies: PoliciesConfig
    workflows: WorkflowsConfig


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigurationError(f"Missing config file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Config root must be a mapping: {path}")
    return data


def find_project_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "config" / "models.yaml").exists():
            return candidate
        if (candidate / ".product-factory" / "config" / "models.yaml").exists():
            return candidate
    return cur


def load_config(root: Path | None = None) -> AppConfig:
    project_root = find_project_root(root)
    config_dir = project_root / "config"
    if not config_dir.exists():
        alt = project_root / ".product-factory" / "config"
        if alt.exists():
            config_dir = alt
        else:
            raise ConfigurationError(
                f"No config directory found under {project_root}. Run `product-factory init`."
            )

    try:
        models = ModelsConfig.model_validate(_load_yaml(config_dir / "models.yaml"))
        policies = PoliciesConfig.model_validate(_load_yaml(config_dir / "policies.yaml"))
        workflows = WorkflowsConfig.model_validate(_load_yaml(config_dir / "workflows.yaml"))
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Failed to load configuration: {exc}") from exc

    return AppConfig(root=project_root, models=models, policies=policies, workflows=workflows)
