#!/usr/bin/env python3
"""Generate registry-backed catalogs into docs/catalogs/."""

from __future__ import annotations

from pathlib import Path

from product_factory.catalogs import write_catalogs


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    written = write_catalogs(root / "docs" / "catalogs")
    for name, path in sorted(written.items()):
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
