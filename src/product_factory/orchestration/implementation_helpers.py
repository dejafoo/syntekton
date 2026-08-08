"""Shared helpers for repository-agent patch extraction and deterministic fixtures."""

from __future__ import annotations


def extract_unified_diff(text: str) -> str:
    """Pull a unified diff out of model output (raw or fenced)."""
    if not text:
        return ""
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            body = part.strip()
            if body.startswith("diff") or body.startswith("--- ") or "\n+++ " in body:
                # strip optional language tag
                lines = body.splitlines()
                if lines and (
                    lines[0].strip().lower() in {"diff", "patch"}
                    or not lines[0].startswith(("diff --git", "--- ", "+++ "))
                ):
                    body = "\n".join(lines[1:]).strip()
                if body.startswith("diff --git") or body.startswith("--- "):
                    return body
    if "diff --git" in cleaned:
        idx = cleaned.index("diff --git")
        return cleaned[idx:].strip()
    if cleaned.startswith("--- ") or "\n+++ " in cleaned:
        return cleaned
    return ""


def deterministic_impl_files(
    request_text: str, *, task_objective: str = ""
) -> list[tuple[str, str]]:
    """
    Request-aware offline/mock implementation.

    Keeps the original health vertical-slice behavior when the request asks for
    health, and produces a simple cache helper when the request asks for cache.
    """
    haystack = f"{request_text}\n{task_objective}".lower()
    if "cache" in haystack:
        return [
            (
                "src/app/cache.py",
                (
                    '"""Cache helper module providing a cache interface and in-memory implementation."""\n\n'
                    "from abc import ABC, abstractmethod\n"
                    "from typing import Any, Optional\n\n\n"
                    "class Cache(ABC):\n"
                    '    """Abstract interface for cache implementations."""\n\n'
                    "    @abstractmethod\n"
                    "    def get(self, key: str) -> Optional[Any]:\n"
                    "        ...\n\n"
                    "    @abstractmethod\n"
                    "    def set(self, key: str, value: Any) -> None:\n"
                    "        ...\n\n"
                    "    @abstractmethod\n"
                    "    def delete(self, key: str) -> None:\n"
                    "        ...\n\n\n"
                    "class InMemoryCache(Cache):\n"
                    '    """Simple in-memory cache implementation backed by a dict."""\n\n'
                    "    def __init__(self) -> None:\n"
                    "        self._store: dict[str, Any] = {}\n\n"
                    "    def get(self, key: str) -> Optional[Any]:\n"
                    "        return self._store.get(key)\n\n"
                    "    def set(self, key: str, value: Any) -> None:\n"
                    "        self._store[key] = value\n\n"
                    "    def delete(self, key: str) -> None:\n"
                    "        self._store.pop(key, None)\n"
                ),
            ),
            (
                "tests/test_cache.py",
                (
                    "from app.cache import InMemoryCache\n\n\n"
                    "def test_in_memory_cache_roundtrip():\n"
                    "    cache = InMemoryCache()\n"
                    '    cache.set("a", 1)\n'
                    '    assert cache.get("a") == 1\n'
                    '    cache.delete("a")\n'
                    '    assert cache.get("a") is None\n'
                ),
            ),
        ]
    if "logging" in haystack:
        return [
            (
                "src/app/logging_util.py",
                (
                    '"""Structured logging helpers."""\n\n'
                    "import json\n"
                    "import logging\n"
                    "from typing import Any\n\n\n"
                    "def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:\n"
                    '    logger.info(json.dumps({"event": event, **fields}, sort_keys=True))\n'
                ),
            )
        ]
    if "retry" in haystack or "jitter" in haystack:
        return [
            (
                "src/app/retry.py",
                (
                    '"""Bounded retry decorator with jitter."""\n\n'
                    "import random\n"
                    "import time\n"
                    "from collections.abc import Callable\n"
                    "from functools import wraps\n"
                    "from typing import ParamSpec, TypeVar\n\n"
                    "P = ParamSpec('P')\n"
                    "R = TypeVar('R')\n\n\n"
                    "def retry(attempts: int = 3, base_delay: float = 0.01):\n"
                    "    def decorate(fn: Callable[P, R]) -> Callable[P, R]:\n"
                    "        @wraps(fn)\n"
                    "        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:\n"
                    "            for attempt in range(attempts):\n"
                    "                try:\n"
                    "                    return fn(*args, **kwargs)\n"
                    "                except Exception:\n"
                    "                    if attempt + 1 == attempts:\n"
                    "                        raise\n"
                    "                    time.sleep(base_delay * (2**attempt) * random.uniform(0.5, 1.5))\n"
                    "            raise AssertionError('unreachable')\n"
                    "        return wrapped\n"
                    "    return decorate\n"
                ),
            )
        ]
    # Default vertical-slice: callable health module (plain package, no HTTP).
    return [
        (
            "src/app/health.py",
            (
                '"""Health check module."""\n\n'
                "def health() -> dict[str, str]:\n"
                '    return {"status": "ok"}\n'
            ),
        ),
        (
            "tests/test_health.py",
            (
                "from app.health import health\n\n"
                "def test_health():\n"
                '    assert health()["status"] == "ok"\n'
            ),
        ),
    ]


__all__ = ["deterministic_impl_files", "extract_unified_diff"]
