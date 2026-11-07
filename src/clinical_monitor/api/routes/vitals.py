from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.application.vitals import VitalSignsService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.vitals import VitalMeasurement

router = APIRouter(prefix="/vital-signs", tags=["vital-signs"])


def get_vital_signs_service(request: Request, ingestion: IngestionDependency) -> VitalSignsService:
    return VitalSignsService(ingestion, request.app.state.vital_ranges)


@router.post(
    "/analyze",
    response_model=Evidence,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": Evidence, "description": "Measurement already analyzed"}},
)
def analyze_vital_sign(
    measurement: VitalMeasurement,
    response: Response,
    service: Annotated[VitalSignsService, Depends(get_vital_signs_service)],
) -> Evidence:
    evidence, created = service.analyze(measurement)
    if not created:
        response.status_code = status.HTTP_200_OK
    return evidence
