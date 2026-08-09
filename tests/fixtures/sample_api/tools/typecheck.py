"""Hermetic fixture syntax validation with no third-party dependencies."""

from __future__ import annotations

import compileall
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
raise SystemExit(0 if compileall.compile_dir(ROOT / "src", quiet=1) else 1)
