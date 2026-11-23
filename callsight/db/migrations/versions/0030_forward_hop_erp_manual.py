"""A ticket close that failed in the other system can be marked "closed there by hand" by staff (forwards page,
"needs action now", 2026-11-23): erp_close may also be 'manual'. Check constraint only.

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-23
"""
from alembic import op

revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_forward_hop_erp_close", "forward_hop", type_="check")
    op.create_check_constraint("ck_forward_hop_erp_close", "forward_hop",
                               "erp_close IS NULL OR erp_close IN ('pending', 'done', 'failed', 'manual')")


def downgrade() -> None:
    op.execute("UPDATE forward_hop SET erp_close = 'failed' WHERE erp_close = 'manual'")
    op.drop_constraint("ck_forward_hop_erp_close", "forward_hop", type_="check")
    op.create_check_constraint("ck_forward_hop_erp_close", "forward_hop",
                               "erp_close IS NULL OR erp_close IN ('pending', 'done', 'failed')")
