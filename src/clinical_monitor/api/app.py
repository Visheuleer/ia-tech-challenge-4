from fastapi import FastAPI

from clinical_monitor.api.routes.health import router as health_router
from clinical_monitor.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(title=settings.app_name)
    app.include_router(health_router)
    return app
