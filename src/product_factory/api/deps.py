"""API dependencies."""

from __future__ import annotations

from pathlib import Path

from product_factory.observability.query import ObservabilityQueryService
from product_factory.persistence.database import Database


class ApiState:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db = Database(data_dir / "data" / "product_factory.sqlite")
        self.query = ObservabilityQueryService(self.db, data_dir=data_dir)

    def close(self) -> None:
        self.db.close()
