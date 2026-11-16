"""forward a problem to a branch supervisor (spec 2026-11-16-priority-no-followup, part 4 D1): table forward_person
and the forward_* columns on call_followup. Additive only.

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
        'forward_person',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('branch', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('ext', sa.String(length=16), nullable=True),
        sa.Column('guid', sa.String(length=64), nullable=True),
        sa.Column('full_name', sa.String(length=200), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('guid'),
        sa.UniqueConstraint('branch', 'name', name='uq_forward_person_branch_name'),
    )
    op.add_column('call_followup', sa.Column('forward_person_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_call_followup_forward_person', 'call_followup', 'forward_person',
                          ['forward_person_id'], ['id'])
    op.add_column('call_followup', sa.Column('forward_note', sa.String(length=500), nullable=True))
    op.add_column('call_followup', sa.Column('forward_status', sa.String(length=10), nullable=True))
    op.add_column('call_followup', sa.Column('forward_ticket_guid', sa.String(length=64), nullable=True))
    op.add_column('call_followup', sa.Column('forward_ticket_number', sa.Integer(), nullable=True))
    op.add_column('call_followup', sa.Column('forwarded_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('call_followup', sa.Column('forward_error', sa.String(length=300), nullable=True))
    op.add_column('call_followup', sa.Column('forward_tries', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint('ck_call_followup_forward_status', 'call_followup',
                               "forward_status IN ('pending', 'sent', 'failed') OR forward_status IS NULL")


def downgrade() -> None:
    op.drop_constraint('ck_call_followup_forward_status', 'call_followup', type_='check')
    for col in ('forward_tries', 'forward_error', 'forwarded_at', 'forward_ticket_number', 'forward_ticket_guid',
                'forward_status', 'forward_note'):
        op.drop_column('call_followup', col)
    op.drop_constraint('fk_call_followup_forward_person', 'call_followup', type_='foreignkey')
    op.drop_column('call_followup', 'forward_person_id')
    op.drop_table('forward_person')
