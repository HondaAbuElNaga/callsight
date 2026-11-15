"""agent's reply to a supervisor's shared call: call_share.reply, reply_at. Additive only.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call_share', sa.Column('reply', sa.String(length=1000), nullable=True))
    op.add_column('call_share', sa.Column('reply_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('call_share', 'reply_at')
    op.drop_column('call_share', 'reply')
