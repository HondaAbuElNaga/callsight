"""follow-up stages (spec 2026-11-15-followup-stages): call_followup_note (notes after a callback, round >= 2).
Additive only.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('call_followup_note',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('cb_call_id', sa.BigInteger(), nullable=True),
    sa.Column('what_happened', sa.String(length=500), nullable=False),
    sa.Column('solved', sa.Boolean(), nullable=False),
    sa.Column('responsible_kind', sa.String(length=20), nullable=True),
    sa.Column('responsible_user_id', sa.Integer(), nullable=True),
    sa.Column('expected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('callback_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('round >= 2', name='ck_call_followup_note_round'),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cb_call_id'], ['call.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['responsible_user_id'], ['app_user.id']),
    sa.ForeignKeyConstraint(['created_by'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'round', name='uq_call_followup_note_round'))
    op.create_index('ix_call_followup_note_call_id', 'call_followup_note', ['call_id'])


def downgrade() -> None:
    op.drop_index('ix_call_followup_note_call_id', table_name='call_followup_note')
    op.drop_table('call_followup_note')
