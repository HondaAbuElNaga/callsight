"""report_download.period_end: the last day (إلى, inclusive) of the downloaded range (spec 2026-11-17-cleanup)

Before, only period_start was kept, so a من/إلى range download could not be told apart from another range with the
same start. Add-only; old rows stay NULL.
"""
from alembic import op
import sqlalchemy as sa

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("report_download", sa.Column("period_end", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("report_download", "period_end")
