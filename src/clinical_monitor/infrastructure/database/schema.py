from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    Uuid,
)

metadata = MetaData()

patients = Table(
    "patients",
    metadata,
    Column("patient_id", Uuid, primary_key=True),
    Column("payload", JSON, nullable=False),
)

encounters = Table(
    "encounters",
    metadata,
    Column("encounter_id", Uuid, primary_key=True),
    Column("patient_id", Uuid, ForeignKey("patients.patient_id"), nullable=False),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("encounter_id", "patient_id", name="uq_encounters_patient"),
    Index("ix_encounters_patient_id", "patient_id"),
)

evidence = Table(
    "evidence",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("encounter_id", Uuid, nullable=False),
    Column("patient_id", Uuid, nullable=False),
    Column("payload", JSON, nullable=False),
    ForeignKeyConstraint(
        ["encounter_id", "patient_id"],
        ["encounters.encounter_id", "encounters.patient_id"],
        name="fk_evidence_encounter_patient",
    ),
    Index("ix_evidence_encounter_patient", "encounter_id", "patient_id"),
)
