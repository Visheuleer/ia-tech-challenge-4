from uuid import UUID

from fastapi import APIRouter, Response, status

from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.domain.patients import Encounter

router = APIRouter(prefix="/encounters", tags=["encounters"])


@router.post(
    "",
    response_model=Encounter,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": Encounter, "description": "Identical record already registered"}},
)
def register_encounter(
    record: Encounter, response: Response, service: IngestionDependency
) -> Encounter:
    created = service.register_encounter(record)
    if not created:
        response.status_code = status.HTTP_200_OK
    return record


@router.get("/{encounter_id}", response_model=Encounter)
def get_encounter(encounter_id: UUID, service: IngestionDependency) -> Encounter:
    return service.get_encounter(encounter_id)
