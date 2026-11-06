from uuid import UUID

from fastapi import APIRouter, Response, status

from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.domain.evidence import Evidence

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.post(
    "",
    response_model=Evidence,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": Evidence, "description": "Identical record already registered"}},
)
def ingest_evidence(record: Evidence, response: Response, service: IngestionDependency) -> Evidence:
    created = service.ingest_evidence(record)
    if not created:
        response.status_code = status.HTTP_200_OK
    return record


@router.get("/{event_id}", response_model=Evidence)
def get_evidence(event_id: UUID, service: IngestionDependency) -> Evidence:
    return service.get_evidence(event_id)
