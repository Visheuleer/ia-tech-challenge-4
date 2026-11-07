from sqlalchemy import Engine

from clinical_monitor.application.ingestion import IngestionService
from clinical_monitor.domain.evidence import Evidence
from clinical_monitor.domain.patients import Encounter, Patient
from clinical_monitor.infrastructure.database import schema
from clinical_monitor.infrastructure.database.repositories import SqlRepository


def create_ingestion_service(engine: Engine) -> IngestionService:
    return IngestionService(
        patients=SqlRepository(engine, schema.patients, Patient, "patient_id"),
        encounters=SqlRepository(engine, schema.encounters, Encounter, "encounter_id"),
        evidence=SqlRepository(engine, schema.evidence, Evidence, "event_id"),
    )
