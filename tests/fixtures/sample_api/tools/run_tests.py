"""Hermetic fixture validation: import the sample API using only stdlib."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    from app.main import hello

    if hello() != "hello":
        raise SystemExit("hello() returned an unexpected value")


if __name__ == "__main__":
    main()
