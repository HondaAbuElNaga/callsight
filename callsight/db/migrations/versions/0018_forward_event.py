"""ticket status back from the customer-service system (spec 2026-11-16-priority-no-followup, part 4 D2): table
forward_event (the ticket's timeline: events, questions, replies; personal data, erased with the call's content) and
forward_closed_at / forward_solution / forward_checked_at on call_followup. Additive only.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'forward_event',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('call_id', sa.BigInteger(), nullable=False),
        sa.Column('ext_key', sa.String(length=40), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('type', sa.String(length=30), nullable=False),
        sa.Column('note', sa.String(length=1000), nullable=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('actor', sa.String(length=200), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('call_id', 'ext_key', name='uq_forward_event_call_key'),
    )
    op.create_index('ix_forward_event_call_id', 'forward_event', ['call_id'])
    op.add_column('call_followup', sa.Column('forward_closed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_followup', sa.Column('forward_solution', sa.String(length=1000), nullable=True))
    op.add_column('call_followup', sa.Column('forward_checked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in ('forward_checked_at', 'forward_solution', 'forward_closed_at'):
        op.drop_column('call_followup', col)
    op.drop_index('ix_forward_event_call_id', table_name='forward_event')
    op.drop_table('forward_event')
