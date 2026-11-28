"""One manager per forwarded problem (user 2026-11-28): forward_person.side ("women" = a manager for the women's
branches only, from the yaml). Add-only.

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-28
"""
from alembic import op
import sqlalchemy as sa

revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("forward_person", sa.Column("side", sa.String(10), nullable=True))
    # the yaml's `side: women` manager right away (the hourly sync sets it too): no hour of the old rule
    op.execute("UPDATE forward_person SET side = 'women' WHERE is_manager AND active AND name = 'احمد حطب'")


def downgrade() -> None:
    op.drop_column("forward_person", "side")
