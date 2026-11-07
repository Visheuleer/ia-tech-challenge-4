from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from clinical_monitor.api.errors import handle_application_error, handle_database_unavailable
from clinical_monitor.api.routes.encounters import router as encounters_router
from clinical_monitor.api.routes.evidence import router as evidence_router
from clinical_monitor.api.routes.health import router as health_router
from clinical_monitor.api.routes.patients import router as patients_router
from clinical_monitor.api.routes.vitals import router as vitals_router
from clinical_monitor.application.errors import ApplicationError
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.core.config import Settings
from clinical_monitor.infrastructure.database.engine import create_database_engine
from clinical_monitor.infrastructure.database.service import create_ingestion_service


def create_app(
    settings: Settings | None = None, *, ingestion_service: IngestionService | None = None
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if ingestion_service is not None:
            app.state.ingestion_service = ingestion_service
            yield
            return
        engine = create_database_engine(settings.database_url.get_secret_value())
        app.state.ingestion_service = create_ingestion_service(engine)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.vital_ranges = settings.vital_ranges
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(OperationalError, handle_database_unavailable)
    app.include_router(health_router)
    app.include_router(patients_router, prefix="/v1")
    app.include_router(encounters_router, prefix="/v1")
    app.include_router(evidence_router, prefix="/v1")
    app.include_router(vitals_router, prefix="/v1")
    return app
