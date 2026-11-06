from fastapi import FastAPI

from clinical_monitor.api.errors import handle_application_error
from clinical_monitor.api.routes.encounters import router as encounters_router
from clinical_monitor.api.routes.evidence import router as evidence_router
from clinical_monitor.api.routes.health import router as health_router
from clinical_monitor.api.routes.patients import router as patients_router
from clinical_monitor.application.errors import ApplicationError
from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.core.config import Settings
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.patients import Encounter, Patient
from clinical_monitor.infrastructure.memory import InMemoryRepository


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(title=settings.app_name)
    app.state.ingestion_service = IngestionService(
        patients=InMemoryRepository[Patient](),
        encounters=InMemoryRepository[Encounter](),
        evidence=InMemoryRepository[Evidence](),
    )
    app.add_exception_handler(ApplicationError, handle_application_error)
    app.include_router(health_router)
    app.include_router(patients_router, prefix="/v1")
    app.include_router(encounters_router, prefix="/v1")
    app.include_router(evidence_router, prefix="/v1")
    return app
