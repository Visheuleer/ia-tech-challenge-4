from fastapi import Request, status
from fastapi.responses import JSONResponse

from clinical_monitor.application.errors import (
    ApplicationError,
    InvalidRelationship,
    RecordConflict,
    RecordNotFound,
)

ERROR_STATUS_CODES = {
    RecordNotFound: status.HTTP_404_NOT_FOUND,
    RecordConflict: status.HTTP_409_CONFLICT,
    InvalidRelationship: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


async def handle_application_error(request: Request, exc: ApplicationError) -> JSONResponse:
    status_code = ERROR_STATUS_CODES.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    detail = str(exc) if status_code != 500 else "Internal server error"
    return JSONResponse(status_code=status_code, content={"detail": detail})
