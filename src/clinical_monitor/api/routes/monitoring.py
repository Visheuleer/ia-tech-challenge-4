from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import ValidationError

from clinical_monitor.analysis.prescriptions import PrescriptionAnalyzer
from clinical_monitor.api.dependencies import IngestionDependency
from clinical_monitor.application.errors import InvalidAnalysis
from clinical_monitor.application.reports import build_report, report_html
from clinical_monitor.domain.monitoring import Alert, AlertAction, ClinicalText, Job, MediaRequest
from clinical_monitor.domain.prescriptions import PrescriptionChange
from clinical_monitor.worker import enqueue

router = APIRouter()


@router.get("/patients", tags=["patients"])
def list_patients(ingestion: IngestionDependency):
    return ingestion.list_patients()


@router.get("/encounters", tags=["encounters"])
def list_encounters(ingestion: IngestionDependency, patient_id: UUID | None = None):
    return ingestion.list_encounters(patient_id)


@router.get("/encounters/{encounter_id}/evidence", tags=["evidence"])
def list_evidence(encounter_id: UUID, ingestion: IngestionDependency):
    ingestion.get_encounter(encounter_id)
    return ingestion.list_evidence(encounter_id)


@router.post("/prescriptions/analyze", tags=["prescriptions"])
def analyze_prescription(
    change: PrescriptionChange, request: Request, ingestion: IngestionDependency
):
    evidence = PrescriptionAnalyzer().analyze(
        change, request.app.state.settings.prescription_relative_change
    )
    return ingestion.ingest_analysis_evidence(evidence)[0]


@router.get("/alerts", response_model=list[Alert], tags=["alerts"])
def list_alerts(request: Request, encounter_id: UUID | None = None, refresh: bool = True):
    if encounter_id is not None and refresh:
        request.app.state.fusion.refresh(encounter_id)
    return request.app.state.fusion.list(encounter_id)


@router.post("/alerts/{alert_id}/actions", response_model=Alert, tags=["alerts"])
def transition_alert(alert_id: UUID, action: AlertAction, request: Request):
    return request.app.state.fusion.transition(alert_id, action)


@router.post("/jobs/text", response_model=Job, status_code=202, tags=["processing"])
def submit_text(record: ClinicalText, request: Request):
    job, created = request.app.state.jobs.submit_text(record)
    if created or job.status == "queued":
        enqueue(job.job_id, job.kind)
    return job


@router.post("/jobs/media", response_model=Job, status_code=202, tags=["processing"])
async def submit_media(
    request: Request,
    metadata: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    try:
        record = MediaRequest.model_validate_json(metadata)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid recording metadata") from exc
    limit = request.app.state.settings.max_upload_mb * 1024 * 1024
    chunks, total = [], 0
    try:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413, detail="File exceeds upload size limit")
            chunks.append(chunk)
    finally:
        await file.close()
    content = b"".join(chunks)
    if record.kind == "audio" and not (content[:4] == b"RIFF" and content[8:12] == b"WAVE"):
        raise InvalidAnalysis("Audio must be PCM WAV")
    job, created = request.app.state.jobs.submit(record, content)
    if created or job.status == "queued":
        enqueue(job.job_id, job.kind)
    return job


@router.get("/jobs", response_model=list[Job], tags=["processing"])
def list_jobs(request: Request, encounter_id: UUID | None = None):
    return request.app.state.jobs.list(encounter_id)


@router.get("/jobs/{job_id}", response_model=Job, tags=["processing"])
def get_job(job_id: UUID, request: Request):
    return request.app.state.jobs.get(job_id)


@router.post("/jobs/{job_id}/retry", response_model=Job, status_code=202, tags=["processing"])
def retry_job(job_id: UUID, request: Request):
    job = request.app.state.jobs.retry(job_id)
    enqueue(job_id, job.kind)
    return job


@router.get("/jobs/{job_id}/artifacts/{name}", tags=["processing"])
def get_artifact(job_id: UUID, name: str, request: Request):
    job = request.app.state.jobs.get(job_id)
    allowed = {"report.json": "application/json", "video-evidence.jpg": "image/jpeg"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="Artifact not found")
    path = request.app.state.jobs.directory(job.job_id) / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not available")
    return FileResponse(path, media_type=allowed[name], filename=name)


@router.get("/encounters/{encounter_id}/report", tags=["reports"])
def get_report(
    encounter_id: UUID,
    request: Request,
    ingestion: IngestionDependency,
    format: Annotated[str, Query(pattern="^(json|html)$")] = "json",
):
    report = build_report(encounter_id, ingestion, request.app.state.fusion, request.app.state.jobs)
    return HTMLResponse(report_html(report)) if format == "html" else report


@router.get("/configuration", tags=["configuration"])
def configuration(request: Request):
    settings = request.app.state.settings
    return {
        "vital_ranges": {key: value.model_dump() for key, value in settings.vital_ranges.items()},
        "vital_temporal_rules": {
            key: value.model_dump() for key, value in settings.vital_temporal_rules.items()
        },
        "azure_speech_configured": bool(
            settings.azure_speech_endpoint
            and settings.azure_speech_key
            and settings.azure_speech_key.get_secret_value()
        ),
        "azure_language_configured": bool(
            settings.azure_language_endpoint
            and settings.azure_language_key
            and settings.azure_language_key.get_secret_value()
        ),
        "fusion_window_seconds": settings.fusion_window_seconds,
    }
