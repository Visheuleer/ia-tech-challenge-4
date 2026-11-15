import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_records",
        sa.Column("record_key", sa.String(120), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column(
            "encounter_id", sa.Uuid(), sa.ForeignKey("encounters.encounter_id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_runtime_kind_encounter", "runtime_records", ["kind", "encounter_id"])


def downgrade() -> None:
    op.drop_table("runtime_records")
