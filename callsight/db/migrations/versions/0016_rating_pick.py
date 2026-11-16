"""rating set (spec 2026-11-16-rating-set): the calls of the days the user chose for rating, picked once.
Additive only.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'rating_pick',
        sa.Column('call_id', sa.BigInteger(), sa.ForeignKey('call.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('rank', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('agent_ext', sa.String(length=16), nullable=True),
        sa.Column('salt', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('rating_pick')
