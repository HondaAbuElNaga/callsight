"""Shared calls: each supervisor/admin has their own archive (table share_staff_close). Add-only: the old shared
column call_share.staff_closed_at stays but is no longer used. Its closes are copied to the staff user who made
them (admin_log action share_close, target "... share:<id>"); a close with no matching log row goes to nobody.

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_staff_close",
        sa.Column("share_id", sa.BigInteger, sa.ForeignKey("call_share.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_share_staff_close_user_id", "share_staff_close", ["user_id"])
    op.execute("""
        INSERT INTO share_staff_close (share_id, user_id, closed_at)
        SELECT DISTINCT ON (sh.id, l.user_id) sh.id, l.user_id, l.at
        FROM call_share sh
        JOIN admin_log l ON l.action = 'share_close' AND l.target LIKE '% share:' || sh.id::text
        JOIN app_user u ON u.id = l.user_id AND u.role IN ('admin', 'supervisor')
        WHERE sh.staff_closed_at IS NOT NULL
        ORDER BY sh.id, l.user_id, l.at
        ON CONFLICT DO NOTHING""")


def downgrade() -> None:
    op.drop_index("ix_share_staff_close_user_id", table_name="share_staff_close")
    op.drop_table("share_staff_close")
