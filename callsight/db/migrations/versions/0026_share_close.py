"""Shared calls tab (spec 2026-11-22-shares-tab): call_share.first_reply_at (24 h reply KPI), agent_closed_at,
staff_closed_at (each side's archive). Add-only. Writes app_setting share_kpi_since = today (Riyadh): shares sent
before this rule existed (before this migration ran) never count against an agent.

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_share", sa.Column("first_reply_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_share", sa.Column("agent_closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_share", sa.Column("staff_closed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE call_share SET first_reply_at = reply_at WHERE reply IS NOT NULL")
    op.execute("""INSERT INTO app_setting (key, value, updated_at)
                  VALUES ('share_kpi_since', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') || '+00:00', now())
                  ON CONFLICT (key) DO NOTHING""")


def downgrade() -> None:
    op.execute("DELETE FROM app_setting WHERE key = 'share_kpi_since'")
    op.drop_column("call_share", "staff_closed_at")
    op.drop_column("call_share", "agent_closed_at")
    op.drop_column("call_share", "first_reply_at")
