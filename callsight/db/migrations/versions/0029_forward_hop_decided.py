"""Supervisor closes a forwarded problem (user decision 2026-11-22): our supervisor / admin picks who in the chain
solved it; that hop becomes "solved" (the KPI point), decided_by / decided_at record who decided. erp_close: the
ticket of an open hop must be closed in the other system by the worker (pending -> done / failed). Add-only.

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0029'
down_revision = '0028'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("forward_hop", sa.Column("decided_by", sa.Integer,
                                           sa.ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True))
    op.add_column("forward_hop", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("forward_hop", sa.Column("erp_close", sa.String(10), nullable=True))
    op.create_check_constraint("ck_forward_hop_erp_close", "forward_hop",
                               "erp_close IS NULL OR erp_close IN ('pending', 'done', 'failed')")
    op.create_index("ix_forward_hop_erp_close", "forward_hop", ["erp_close"],
                    postgresql_where=sa.text("erp_close = 'pending'"))


def downgrade() -> None:
    op.drop_index("ix_forward_hop_erp_close", table_name="forward_hop")
    op.drop_constraint("ck_forward_hop_erp_close", "forward_hop", type_="check")
    op.drop_column("forward_hop", "erp_close")
    op.drop_column("forward_hop", "decided_at")
    op.drop_column("forward_hop", "decided_by")
