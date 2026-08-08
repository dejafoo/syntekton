"""Aggregate repository base for the serialized SQLite actor."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from product_factory.persistence.connection import SqliteActor


def synchronized[F: Callable[..., Any]](method: F) -> F:
    """Serialize a repository method on the owning SqliteActor lock."""

    def wrapper(self: AggregateRepository, *args: Any, **kwargs: Any) -> Any:
        return self._actor.run(lambda _conn: method(self, *args, **kwargs))

    wrapper.__name__ = getattr(method, "__name__", "wrapper")
    wrapper.__doc__ = method.__doc__
    return wrapper  # type: ignore[return-value]


class AggregateRepository:
    """Base for aggregate repositories bound to one SqliteActor."""

    def __init__(self, actor: SqliteActor) -> None:
        self._actor = actor

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._actor.connection
