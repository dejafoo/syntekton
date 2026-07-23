"""Named repairable defects for the seeded-repair evaluation gate."""

from __future__ import annotations

from typing import Literal

DefectKind = Literal["broken_syntax", "failing_test", "incomplete_impl"]

# Default defect kind per Stage B case (3 seeds × 4 cases = 12 live cells).
# Prefer defects whose accompanying tests require real behavior after syntax is fixed.
CASE_DEFAULT_DEFECT: dict[str, DefectKind] = {
    "code_cache": "failing_test",
    "code_health": "failing_test",
    "code_logging": "incomplete_impl",
    "code_retry": "failing_test",
}


def resolve_defect_kind(case_id: str, *, explicit: str | None = None) -> DefectKind:
    if explicit in {"broken_syntax", "failing_test", "incomplete_impl"}:
        return explicit  # type: ignore[return-value]
    return CASE_DEFAULT_DEFECT.get(case_id, "broken_syntax")


def defect_files(case_id: str, kind: DefectKind) -> list[tuple[str, str]]:
    """Return (relative_path, content) pairs that should fail smoke validation."""
    if case_id == "code_cache":
        if kind == "failing_test":
            return [
                (
                    "src/app/cache.py",
                    (
                        '"""Broken cache helper for seeded repair."""\n\n'
                        "class InMemoryCache:\n"
                        "    def __init__(self) -> None:\n"
                        "        self._store: dict[str, object] = {}\n\n"
                        "    def get(self, key: str) -> object | None:\n"
                        "        return None\n\n"
                        "    def set(self, key: str, value: object) -> None:\n"
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
                    ),
                ),
            ]
        if kind == "incomplete_impl":
            return [
                (
                    "src/app/cache.py",
                    '"""Incomplete cache helper."""\n\n',
                ),
                (
                    "tests/test_cache.py",
                    (
                        "from app.cache import InMemoryCache\n\n\n"
                        "def test_in_memory_cache_roundtrip():\n"
                        "    cache = InMemoryCache()\n"
                        '    cache.set("a", 1)\n'
                        '    assert cache.get("a") == 1\n'
                    ),
                ),
            ]
        return [
            (
                "src/app/cache.py",
                (
                    '"""Broken cache helper for seeded repair."""\n\n'
                    "class InMemoryCache:\n"
                    "    def get(self, key: str\n"
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

    if case_id == "code_health":
        if kind == "broken_syntax":
            return [
                (
                    "src/app/health.py",
                    '"""Broken health module."""\n\ndef health(\n',
                ),
                (
                    "tests/test_health.py",
                    (
                        "from app.health import health\n\n\n"
                        "def test_health():\n"
                        '    assert health()["status"] == "ok"\n'
                    ),
                ),
            ]
        if kind == "incomplete_impl":
            return [
                (
                    "src/app/health.py",
                    '"""Incomplete health module."""\n',
                ),
                (
                    "tests/test_health.py",
                    (
                        "from app.health import health\n\n\n"
                        "def test_health():\n"
                        '    assert health()["status"] == "ok"\n'
                    ),
                ),
            ]
        return [
            (
                "src/app/health.py",
                (
                    '"""Seeded failing health check."""\n\n'
                    "def health() -> dict[str, str]:\n"
                    '    return {"status": "bad"}\n'
                ),
            ),
            (
                "tests/test_health.py",
                (
                    "from app.health import health\n\n\n"
                    "def test_health():\n"
                    '    assert health()["status"] == "ok"\n'
                ),
            ),
        ]

    if case_id == "code_logging":
        if kind == "broken_syntax":
            return [
                (
                    "src/app/logging_util.py",
                    '"""Broken logging helper."""\n\ndef log_event(logger, event\n',
                ),
                (
                    "tests/test_logging_util.py",
                    (
                        "import logging\n"
                        "from app.logging_util import log_event\n\n\n"
                        "def test_log_event():\n"
                        "    log_event(logging.getLogger('t'), 'x', k=1)\n"
                    ),
                ),
            ]
        if kind == "failing_test":
            return [
                (
                    "src/app/logging_util.py",
                    (
                        '"""Seeded logging helper with wrong contract."""\n\n'
                        "def log_event(logger, event: str, **fields: object) -> None:\n"
                        "    raise AssertionError('seeded failure')\n"
                    ),
                ),
                (
                    "tests/test_logging_util.py",
                    (
                        "import logging\n"
                        "from app.logging_util import log_event\n\n\n"
                        "def test_log_event():\n"
                        "    log_event(logging.getLogger('t'), 'x', k=1)\n"
                    ),
                ),
            ]
        return [
            (
                "src/app/logging_util.py",
                '"""Incomplete logging helper."""\n',
            ),
            (
                "tests/test_logging_util.py",
                (
                    "import logging\n"
                    "from app.logging_util import log_event\n\n\n"
                    "def test_log_event():\n"
                    "    log_event(logging.getLogger('t'), 'x', k=1)\n"
                ),
            ),
        ]

    # code_retry and fallback
    if kind == "failing_test":
        return [
            (
                "src/app/retry.py",
                (
                    '"""Seeded retry helper that never retries."""\n\n'
                    "from collections.abc import Callable\n"
                    "from typing import TypeVar\n\n"
                    "F = TypeVar('F', bound=Callable)\n\n\n"
                    "def retry(attempts: int = 3, base_delay: float = 0.01):\n"
                    "    def decorate(fn: F) -> F:\n"
                    "        return fn\n"
                    "    return decorate\n"
                ),
            ),
            (
                "tests/test_retry.py",
                (
                    "from app.retry import retry\n\n\n"
                    "def test_retry_retries():\n"
                    "    calls = {'n': 0}\n\n"
                    "    @retry(attempts=3, base_delay=0.0)\n"
                    "    def flaky() -> str:\n"
                    "        calls['n'] += 1\n"
                    "        if calls['n'] < 2:\n"
                    "            raise RuntimeError('fail')\n"
                    "        return 'ok'\n\n"
                    "    assert flaky() == 'ok'\n"
                    "    assert calls['n'] == 2\n"
                ),
            ),
        ]
    if kind == "incomplete_impl":
        return [
            (
                "src/app/retry.py",
                '"""Incomplete retry helper."""\n',
            ),
            (
                "tests/test_retry.py",
                (
                    "from app.retry import retry\n\n\n"
                    "def test_retry_retries():\n"
                    "    calls = {'n': 0}\n\n"
                    "    @retry(attempts=3, base_delay=0.0)\n"
                    "    def flaky() -> str:\n"
                    "        calls['n'] += 1\n"
                    "        if calls['n'] < 2:\n"
                    "            raise RuntimeError('fail')\n"
                    "        return 'ok'\n\n"
                    "    assert flaky() == 'ok'\n"
                    "    assert calls['n'] == 2\n"
                ),
            ),
        ]
    return [
        (
            "src/app/retry.py",
            '"""Broken retry helper."""\n\ndef retry(attempts: int = 3\n',
        ),
        (
            "tests/test_retry.py",
            (
                "from app.retry import retry\n\n\n"
                "def test_retry_retries():\n"
                "    calls = {'n': 0}\n\n"
                "    @retry(attempts=3, base_delay=0.0)\n"
                "    def flaky() -> str:\n"
                "        calls['n'] += 1\n"
                "        if calls['n'] < 2:\n"
                "            raise RuntimeError('fail')\n"
                "        return 'ok'\n\n"
                "    assert flaky() == 'ok'\n"
                "    assert calls['n'] == 2\n"
            ),
        ),
    ]
