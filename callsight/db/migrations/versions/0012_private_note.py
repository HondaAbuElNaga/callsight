"""private note per call (spec 2026-11-15-private-note): call_private_note. Additive only.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('call_private_note',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.String(length=300), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'user_id', name='uq_call_private_note'))
    op.create_index('ix_call_private_note_call_id', 'call_private_note', ['call_id'])
    op.create_index('ix_call_private_note_user_id', 'call_private_note', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_call_private_note_user_id', table_name='call_private_note')
    op.drop_index('ix_call_private_note_call_id', table_name='call_private_note')
    op.drop_table('call_private_note')
