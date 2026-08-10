"""Application composition — dependency construction for Product Factory."""

from product_factory.application.command_service import LifecycleCommandService
from product_factory.application.composition_root import (
    ApplicationServices,
    ProductFactoryApplication,
    build_application,
    build_coordinator,
    build_host_service,
)
from product_factory.application.ports import RunLifecyclePort

__all__ = [
    "ApplicationServices",
    "LifecycleCommandService",
    "ProductFactoryApplication",
    "RunLifecyclePort",
    "build_application",
    "build_coordinator",
    "build_host_service",
]
