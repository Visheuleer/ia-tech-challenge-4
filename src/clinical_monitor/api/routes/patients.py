from uuid import UUID

from fastapi import APIRouter, Response, status

from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.domain.patients import Patient

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post(
    "",
    response_model=Patient,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": Patient, "description": "Identical record already registered"}},
)
def register_patient(record: Patient, response: Response, service: IngestionDependency) -> Patient:
    created = service.register_patient(record)
    if not created:
        response.status_code = status.HTTP_200_OK
    return record


@router.get("/{patient_id}", response_model=Patient)
def get_patient(patient_id: UUID, service: IngestionDependency) -> Patient:
    return service.get_patient(patient_id)
