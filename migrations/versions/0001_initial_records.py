import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patients",
        sa.Column("patient_id", sa.Uuid(), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "encounters",
        sa.Column("encounter_id", sa.Uuid(), primary_key=True),
        sa.Column("patient_id", sa.Uuid(), sa.ForeignKey("patients.patient_id"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("encounter_id", "patient_id", name="uq_encounters_patient"),
    )
    op.create_index("ix_encounters_patient_id", "encounters", ["patient_id"])
    op.create_table(
        "evidence",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("encounter_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["encounter_id", "patient_id"],
            ["encounters.encounter_id", "encounters.patient_id"],
            name="fk_evidence_encounter_patient",
        ),
    )
    op.create_index("ix_evidence_encounter_patient", "evidence", ["encounter_id", "patient_id"])


def downgrade() -> None:
    op.drop_table("evidence")
    op.drop_table("encounters")
    op.drop_table("patients")
