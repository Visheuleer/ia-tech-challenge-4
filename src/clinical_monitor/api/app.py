from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from clinical_monitor.api.errors import handle_application_error, handle_database_unavailable
from clinical_monitor.api.routes.encounters import router as encounters_router
from clinical_monitor.api.routes.evidence import router as evidence_router
from clinical_monitor.api.routes.health import router as health_router
from clinical_monitor.api.routes.monitoring import router as monitoring_router
from clinical_monitor.api.routes.patients import router as patients_router
from clinical_monitor.api.routes.vital_series import router as vital_series_router
from clinical_monitor.api.routes.vitals import router as vitals_router
from clinical_monitor.api.security import authenticate
from clinical_monitor.application.errors import ApplicationError
from clinical_monitor.application.fusion import FusionService
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.application.jobs import JobService
from clinical_monitor.core.config import Settings
from clinical_monitor.infrastructure.database.engine import create_database_engine
from clinical_monitor.infrastructure.database.service import create_ingestion_service
from clinical_monitor.infrastructure.runtime import MemoryRuntimeStore, SqlRuntimeStore


def create_app(
    settings: Settings | None = None, *, ingestion_service: IngestionService | None = None
) -> FastAPI:
    settings = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = None
        if ingestion_service is not None:
            app.state.ingestion_service = ingestion_service
            store = MemoryRuntimeStore()
        else:
            engine = create_database_engine(settings.database_url.get_secret_value())
            app.state.ingestion_service = create_ingestion_service(engine)
            store = SqlRuntimeStore(engine)
        app.state.fusion = FusionService(
            app.state.ingestion_service, store, settings.fusion_window_seconds
        )
        app.state.ingestion_service.observe_evidence(app.state.fusion.refresh)
        app.state.jobs = JobService(store, app.state.ingestion_service, settings)
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.middleware("http")(authenticate)
    app.state.vital_ranges = settings.vital_ranges
    app.state.vital_temporal_rules = settings.vital_temporal_rules
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(OperationalError, handle_database_unavailable)
    app.include_router(health_router)
    app.include_router(patients_router, prefix="/v1")
    app.include_router(encounters_router, prefix="/v1")
    app.include_router(evidence_router, prefix="/v1")
    app.include_router(vitals_router, prefix="/v1")
    app.include_router(vital_series_router, prefix="/v1")
    app.include_router(monitoring_router, prefix="/v1")
    return app
