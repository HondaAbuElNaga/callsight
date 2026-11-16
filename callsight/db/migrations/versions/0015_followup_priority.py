"""follow-up priority (spec 2026-11-16-priority-no-followup): call_followup.priority high / medium / low, set in the
first form; old forms and "no follow-up" forms stay NULL. Additive only.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call_followup', sa.Column('priority', sa.String(length=10), nullable=True))
    op.create_check_constraint('ck_call_followup_priority', 'call_followup',
                               "priority IN ('high', 'medium', 'low') OR priority IS NULL")


def downgrade() -> None:
    op.drop_constraint('ck_call_followup_priority', 'call_followup', type_='check')
    op.drop_column('call_followup', 'priority')
