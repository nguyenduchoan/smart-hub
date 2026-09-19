"""Dashboard API routes."""
from .catalog import router as catalog_router
from .devices import router as devices_router
from .gateways import router as gateways_router
from .recording import router as recording_router
from .samples import router as samples_router
from .system import router as system_router
from .wake import router as wake_router

__all__ = [
    "catalog_router",
    "devices_router",
    "gateways_router",
    "recording_router",
    "samples_router",
    "system_router",
    "wake_router",
]
