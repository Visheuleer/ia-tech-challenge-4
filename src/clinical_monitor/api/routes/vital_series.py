from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.application.vital_series import VitalSeriesService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.vital_series import VitalSeries

router = APIRouter(prefix="/vital-signs", tags=["vital-signs"])


def get_series_service(request: Request, ingestion: IngestionDependency) -> VitalSeriesService:
    return VitalSeriesService(
        ingestion, request.app.state.vital_ranges, request.app.state.vital_temporal_rules
    )


@router.post(
    "/analyze-series",
    response_model=Evidence,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": Evidence, "description": "Series already analyzed"}},
)
def analyze_series(
    series: VitalSeries,
    response: Response,
    service: Annotated[VitalSeriesService, Depends(get_series_service)],
) -> Evidence:
    evidence, created = service.analyze(series)
    if not created:
        response.status_code = status.HTTP_200_OK
    return evidence
