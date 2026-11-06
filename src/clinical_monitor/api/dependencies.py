from typing import Annotated, cast

from fastapi import Depends, Request

from clinical_monitor.application.ingestion import IngestionService


def get_ingestion_service(request: Request) -> IngestionService:
    return cast(IngestionService, request.app.state.ingestion_service)


IngestionDependency = Annotated[IngestionService, Depends(get_ingestion_service)]
