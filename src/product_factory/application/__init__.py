"""Application composition — dependency construction for Product Factory."""

from product_factory.application.command_service import LifecycleCommandService
from product_factory.application.composition_root import (
    ApplicationServices,
    build_application,
)

__all__ = ["ApplicationServices", "LifecycleCommandService", "build_application"]
