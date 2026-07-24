"""Host integration protocol and shared service layer (Phase 3)."""

from product_factory.host.protocol import (
    HOST_PROTOCOL,
    HostError,
    HostResponse,
    HostSubscription,
)
from product_factory.host.service import HostService

__all__ = [
    "HOST_PROTOCOL",
    "HostError",
    "HostResponse",
    "HostService",
    "HostSubscription",
]
