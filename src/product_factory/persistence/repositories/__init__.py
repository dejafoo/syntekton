"""Aggregate repositories for Product Factory SQLite persistence (SD3.A)."""

from product_factory.persistence.repositories.approvals import ApprovalRepository
from product_factory.persistence.repositories.artifacts import ArtifactRepository
from product_factory.persistence.repositories.evaluations import EvaluationRepository
from product_factory.persistence.repositories.events import EventRepository
from product_factory.persistence.repositories.handoffs import HandoffRepository
from product_factory.persistence.repositories.runs import RunRepository
from product_factory.persistence.repositories.tasks import TaskRepository
from product_factory.persistence.repositories.workers import WorkerRepository

__all__ = [
    "ApprovalRepository",
    "ArtifactRepository",
    "EvaluationRepository",
    "EventRepository",
    "HandoffRepository",
    "RunRepository",
    "TaskRepository",
    "WorkerRepository",
]
