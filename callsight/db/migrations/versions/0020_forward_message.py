"""what we write on a forwarded ticket (spec 2026-11-16-priority-no-followup, part 4 D3): table forward_message
(the agent's answers to the employee's questions, update notes, the call's AI summary once, "seen" marks). Personal
data, erased with the call's content. Additive only.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'forward_message',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('call_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('text', sa.String(length=600), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
        sa.Column('upto_event_id', sa.BigInteger(), nullable=True),
        sa.Column('sent_to', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint("kind IN ('answer', 'note', 'summary', 'seen')", name='ck_forward_message_kind'),
        sa.CheckConstraint("status IN ('pending', 'waiting', 'sent', 'failed', 'done')",
                           name='ck_forward_message_status'),
        sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['app_user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_forward_message_call_id', 'forward_message', ['call_id'])
    op.create_index('ix_forward_message_status', 'forward_message', ['status'])
    op.create_index('uq_forward_message_summary', 'forward_message', ['call_id'], unique=True,
                    postgresql_where=sa.text("kind = 'summary'"))


def downgrade() -> None:
    op.drop_index('uq_forward_message_summary', table_name='forward_message')
    op.drop_index('ix_forward_message_status', table_name='forward_message')
    op.drop_index('ix_forward_message_call_id', table_name='forward_message')
    op.drop_table('forward_message')
