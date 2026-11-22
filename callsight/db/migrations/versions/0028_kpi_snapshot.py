"""KPI snapshots (retention, spec 2026-11-17-legal-retention): the numbers of a closed week / month per scope, saved
before retention erases the first call of that period, so old reports stay the same. Add-only.

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kpi_snapshot",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("start", sa.Date, nullable=False),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("data", JSONB, nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "start", "scope", name="uq_kpi_snapshot"),
    )


def downgrade() -> None:
    op.drop_table("kpi_snapshot")
