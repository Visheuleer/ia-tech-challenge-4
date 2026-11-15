import logging
from datetime import UTC, datetime
from uuid import UUID

from celery import Celery

from clinical_monitor.application.fusion import FusionService
from clinical_monitor.application.jobs import JobService
from clinical_monitor.application.processing import MediaProcessor
from clinical_monitor.core.config import Settings
from clinical_monitor.infrastructure.database.engine import create_database_engine
from clinical_monitor.infrastructure.database.service import create_ingestion_service
from clinical_monitor.infrastructure.runtime import SqlRuntimeStore

settings = Settings()
celery_app = Celery("clinical_monitor", broker=settings.redis_url)
celery_app.conf.update(
    task_default_queue="media",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_connection_timeout=2,
    task_publish_retry=False,
    task_ignore_result=True,
    broker_transport_options={
        "visibility_timeout": settings.task_lease_seconds * 2,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    beat_schedule={
        "reconcile": {
            "task": "clinical.reconcile",
            "schedule": 5.0,
            "options": {"queue": "clinical"},
        }
    },
)


def enqueue(job_id: UUID, kind: str = "video") -> bool:
    try:
        process_media.apply_async(
            args=[str(job_id)], queue="clinical" if kind == "text" else "media"
        )
        return True
    except Exception:
        logging.getLogger(__name__).warning("Job %s remains queued for reconciliation", job_id)
        return False


@celery_app.task(name="media.process", soft_time_limit=1500, time_limit=1600)
def process_media(job_id: str) -> None:
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        ingestion = create_ingestion_service(engine)
        store = SqlRuntimeStore(engine)
        fusion = FusionService(ingestion, store, settings.fusion_window_seconds)
        ingestion.observe_evidence(fusion.refresh)
        MediaProcessor(JobService(store, ingestion, settings), settings).run(UUID(job_id))
    finally:
        engine.dispose()


@celery_app.task(name="clinical.reconcile")
def reconcile() -> None:
    engine = create_database_engine(settings.database_url.get_secret_value())
    try:
        ingestion = create_ingestion_service(engine)
        store = SqlRuntimeStore(engine)
        jobs = JobService(store, ingestion, settings)
        now = datetime.now(UTC)
        for job in jobs.list():
            expired = (now - job.updated_at).total_seconds() > settings.task_lease_seconds
            if job.status == "queued" or job.status == "running" and expired:
                enqueue(job.job_id, job.kind)
        fusion = FusionService(ingestion, store, settings.fusion_window_seconds)
        for encounter in ingestion.list_encounters():
            fusion.refresh(encounter.encounter_id)
    finally:
        engine.dispose()
