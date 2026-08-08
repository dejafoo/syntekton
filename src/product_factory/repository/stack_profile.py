"""Deterministic repository stack-profile discovery."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

StackProfileStatus = Literal["known", "limited", "unknown"]

_PYTHON_FRAMEWORKS = ("django", "fastapi", "flask", "pydantic")
_JAVASCRIPT_FRAMEWORKS = ("express", "next", "react", "svelte", "vue")


class FrameworkSlot(BaseModel):
    """A framework identified from declared dependencies."""

    model_config = {"extra": "forbid"}

    name: str
    location_globs: list[str] = Field(default_factory=list)


class LanguageStack(BaseModel):
    """Bounded language/runtime and repository-location facts."""

    model_config = {"extra": "forbid"}

    language: Literal["python", "javascript", "typescript"]
    runtime: str | None = None
    frameworks: list[FrameworkSlot] = Field(default_factory=list)
    location_globs: list[str] = Field(default_factory=list)


class StackProfile(BaseModel):
    """Compact repository-derived profile; no model-inferred tree prose."""

    model_config = {"extra": "forbid"}

    id: str
    version: str = "1.0.0"
    kind: Literal["stack"] = "stack"
    status: StackProfileStatus
    languages: list[LanguageStack] = Field(default_factory=list)
    registered_command_ids: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @property
    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def as_manifest_entry(self) -> dict[str, str]:
        return {f"stack:{self.id}": self.digest}


def _normalized_dependency_name(specifier: str) -> str:
    return re.split(r"[\s\[<>=!~;@]", specifier.strip().lower(), maxsplit=1)[0]


def _framework_slots(
    names: set[str], candidates: tuple[str, ...], locations: list[str]
) -> list[FrameworkSlot]:
    return [
        FrameworkSlot(name=name, location_globs=list(locations))
        for name in candidates
        if name in names
    ]


def _relevant_commands(command_ids: Iterable[str], language: str) -> list[str]:
    markers = {
        "python": ("python", "pytest", "pyright", "ruff", "mypy"),
        "javascript": ("javascript", "js_", "node", "npm", "eslint", "vitest", "jest"),
        "typescript": (
            "javascript",
            "js_",
            "typescript",
            "ts_",
            "node",
            "npm",
            "eslint",
            "vitest",
            "jest",
        ),
    }[language]
    return sorted(
        {
            command_id
            for command_id in command_ids
            if any(marker in command_id.lower() for marker in markers)
        }
    )


def _profile_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or "repository"


def discover_stack_profile(
    root: Path,
    *,
    registered_command_ids: Iterable[str] = (),
    inventory: Any | None = None,
) -> StackProfile:
    """Discover only declared Python/JS stack facts from fixed manifest names.

    Manifest reads are confined by SafeRepositoryInventory (SD0.E).
    """
    from product_factory.context.safe_inventory import (
        InventoryPolicy,
        SafeRepositoryInventory,
        build_safe_repository_inventory,
    )

    root = Path(root)
    if inventory is None:
        inventory = build_safe_repository_inventory(
            root,
            policy=InventoryPolicy(max_files=500, max_file_bytes=1_000_000),
        )
    elif not isinstance(inventory, SafeRepositoryInventory):
        raise TypeError("inventory must be a SafeRepositoryInventory")

    languages: list[LanguageStack] = []
    sources: list[str] = []
    limitations: list[str] = []
    profile_name = root.name

    def _safe_text(rel: str) -> str | None:
        if not inventory.contains(rel):
            return None
        try:
            return inventory.read_text(rel)
        except (OSError, UnicodeDecodeError, PermissionError, FileNotFoundError):
            return None

    def _safe_dir(rel: str) -> bool:
        path = root / rel
        return path.is_dir() and not path.is_symlink()

    python_dependencies: set[str] = set()
    python_runtime: str | None = None
    pyproject_text = _safe_text("pyproject.toml")
    if pyproject_text is not None:
        sources.append("pyproject.toml")
        try:
            data = tomllib.loads(pyproject_text)
            project = data.get("project") or {}
            profile_name = str(project.get("name") or profile_name)
            python_runtime = project.get("requires-python")
            declared = list(project.get("dependencies") or [])
            for values in (project.get("optional-dependencies") or {}).values():
                declared.extend(values or [])
            python_dependencies.update(_normalized_dependency_name(str(item)) for item in declared)
        except (tomllib.TOMLDecodeError, AttributeError):
            limitations.append("pyproject.toml could not be parsed")

    uv_text = _safe_text("uv.lock")
    if uv_text is not None:
        sources.append("uv.lock")
        try:
            lock_data = tomllib.loads(uv_text)
            for package in lock_data.get("package") or []:
                if isinstance(package, dict) and package.get("name"):
                    python_dependencies.add(str(package["name"]).lower())
            python_runtime = python_runtime or lock_data.get("requires-python")
        except (tomllib.TOMLDecodeError, AttributeError):
            limitations.append("uv.lock could not be parsed")

    if pyproject_text is not None or uv_text is not None:
        python_locations: list[str] = []
        if _safe_dir("src"):
            python_locations.append("src/**/*.py")
        if _safe_dir("tests"):
            python_locations.append("tests/**/*.py")
        if not python_locations:
            python_locations.append("**/*.py")
        languages.append(
            LanguageStack(
                language="python",
                runtime=str(python_runtime) if python_runtime else None,
                frameworks=_framework_slots(
                    python_dependencies, _PYTHON_FRAMEWORKS, python_locations
                ),
                location_globs=python_locations,
            )
        )
        if pyproject_text is None:
            limitations.append("Python project manifest is absent")

    package_text = _safe_text("package.json")
    if package_text is not None:
        sources.append("package.json")
        try:
            package_data: Any = json.loads(package_text)
            if not isinstance(package_data, dict):
                raise ValueError("package.json is not an object")
            profile_name = str(package_data.get("name") or profile_name)
            dependencies = {
                str(name).lower()
                for section in ("dependencies", "devDependencies", "peerDependencies")
                for name in (package_data.get(section) or {})
            }
            is_typescript = "typescript" in dependencies
            language: Literal["javascript", "typescript"] = (
                "typescript" if is_typescript else "javascript"
            )
            suffix = "{ts,tsx}" if is_typescript else "{js,jsx,mjs,cjs}"
            locations: list[str] = []
            for directory in ("src", "app", "test", "tests"):
                if _safe_dir(directory):
                    locations.append(f"{directory}/**/*.{suffix}")
            if not locations:
                locations.append(f"**/*.{suffix}")
            engines = package_data.get("engines") or {}
            runtime = engines.get("node") if isinstance(engines, dict) else None
            languages.append(
                LanguageStack(
                    language=language,
                    runtime=str(runtime) if runtime else None,
                    frameworks=_framework_slots(dependencies, _JAVASCRIPT_FRAMEWORKS, locations),
                    location_globs=locations,
                )
            )
            if runtime is None:
                limitations.append("Node runtime is not declared")
        except (json.JSONDecodeError, ValueError, TypeError):
            limitations.append("package.json could not be parsed")

    if not languages:
        status: StackProfileStatus = "unknown"
        limitations.append("No supported manifest or lockfile found")
    elif len(languages) > 1:
        status = "limited"
        limitations.append("Multiple fixture-language stacks require explicit selection")
    elif limitations:
        status = "limited"
    else:
        status = "known"

    commands = sorted(
        {
            command_id
            for language in languages
            for command_id in _relevant_commands(registered_command_ids, language.language)
        }
    )
    return StackProfile(
        id=_profile_id(profile_name),
        status=status,
        languages=languages,
        registered_command_ids=commands,
        source_files=sorted(sources),
        limitations=sorted(set(limitations)),
    )
