"""Configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.connectors.policy import ConnectorsConfig, load_connectors_config
from product_factory.domain.budgets import BudgetsConfig
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


class ContextPackingConfig(BaseModel):
    """Operator-tunable limits for repository excerpt / prompt packing."""

    max_excerpt_files: int = Field(default=12, ge=1)
    min_excerpt_chars: int = Field(default=4_000, ge=0)
    max_excerpt_chars: int = Field(default=20_000, ge=1)
    max_file_list_paths: int = Field(default=80, ge=1)
    max_manifest_excerpts: int = Field(default=20, ge=1)
    min_manifest_chars: int = Field(default=2_000, ge=0)
    max_manifest_chars: int = Field(default=40_000, ge=1)
    clamp_to_model_window: bool = True
    chars_per_token: int = Field(default=4, ge=1)
    model_window_reserve_ratio: float = Field(default=0.45, ge=0.0, le=0.9)


class PoliciesConfig(BaseModel):
    allow_dirty_repo: bool = False
    max_artifact_bytes: int = 5_000_000
    prohibited_path_globs: list[str] = Field(
        default_factory=lambda: [".env", "**/.env", "**/secrets/**"]
    )
    budgets: BudgetsConfig = Field(default_factory=BudgetsConfig)
    context: ContextPackingConfig = Field(default_factory=ContextPackingConfig)
    registered_commands: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WorkflowsConfig(BaseModel):
    default_workflow: str = "code_change"
    workflows: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AppConfig(BaseModel):
    root: Path
    models: ModelsConfig
    policies: PoliciesConfig
    workflows: WorkflowsConfig
    # `connectors.yaml` is optional; a missing file means no connector is enabled.
    connectors: ConnectorsConfig = ConnectorsConfig()


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
        connectors = load_connectors_config(config_dir)
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Failed to load configuration: {exc}") from exc

    return AppConfig(
        root=project_root,
        models=models,
        policies=policies,
        workflows=workflows,
        connectors=connectors,
    )
