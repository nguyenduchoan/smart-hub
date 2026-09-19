"""FastAPI web application factory for Smart Hub Dashboard."""
from contextlib import asynccontextmanager
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..devices import DeviceStorage
from ..devices.catalogs.seed_data import get_seed_code_sets
from .routes import (
    catalog_router,
    devices_router,
    gateways_router,
    recording_router,
    samples_router,
    system_router,
    wake_router,
)
from .security import HostOriginSecurityMiddleware

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = DeviceStorage()
    storage.recover_interrupted_commands()
    # R04: Do not seed synthetic dummy codes into production database unless mock hardware is active
    if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
        if not storage.list_code_sets():
            for cs in get_seed_code_sets():
                storage.save_code_set(cs)
    yield


def create_app(allowed_hosts=None) -> FastAPI:
    app = FastAPI(
        title="Smart Hub Dashboard",
        description="Dashboard local: Broadlink RM4 mini, Thu âm và Wake Word Model",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # Security middleware
    app.add_middleware(HostOriginSecurityMiddleware, allowed_hosts=allowed_hosts)

    # Mount API routers
    app.include_router(system_router)
    app.include_router(gateways_router)
    app.include_router(devices_router)
    app.include_router(catalog_router)
    app.include_router(recording_router)
    app.include_router(samples_router)
    app.include_router(wake_router)

    # Static assets
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        index_html = STATIC_DIR / "index.html"
        if not index_html.exists():
            return {"message": "Smart Hub Dashboard API is running. UI index.html not found."}
        return FileResponse(index_html, media_type="text/html")

    return app
