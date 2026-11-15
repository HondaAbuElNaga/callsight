"""agent + supervisor work (spec 2026-11-15-agent-supervisor): call.content_deleted_at, call_followup, call_share,
app_setting, admin_log. Additive only.

Before running on the live DB: backup first (python -m callsight backup).

Revision ID: 0010
Revises: 0008
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call', sa.Column('content_deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('call_followup',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('owner_ext', sa.String(length=16), nullable=False),
    sa.Column('no_followup', sa.Boolean(), nullable=False),
    sa.Column('what_happened', sa.String(length=500), nullable=True),
    sa.Column('solved', sa.Boolean(), nullable=True),
    sa.Column('responsible_kind', sa.String(length=20), nullable=True),
    sa.Column('responsible_user_id', sa.Integer(), nullable=True),
    sa.Column('expected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('callback_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('filled_by', sa.Integer(), nullable=False),
    sa.Column('first_filled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['responsible_user_id'], ['app_user.id']),
    sa.ForeignKeyConstraint(['filled_by'], ['app_user.id']),
    sa.ForeignKeyConstraint(['updated_by'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id'))
    op.create_index('ix_call_followup_owner_ext', 'call_followup', ['owner_ext'])
    op.create_table('call_share',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('from_user_id', sa.Integer(), nullable=False),
    sa.Column('to_user_id', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=1000), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['from_user_id'], ['app_user.id']),
    sa.ForeignKeyConstraint(['to_user_id'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_call_share_call_id', 'call_share', ['call_id'])
    op.create_index('ix_call_share_to_user_id', 'call_share', ['to_user_id'])
    op.create_table('app_setting',
    sa.Column('key', sa.String(length=50), nullable=False),
    sa.Column('value', sa.String(length=200), nullable=False),
    sa.Column('updated_by', sa.Integer(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['updated_by'], ['app_user.id']),
    sa.PrimaryKeyConstraint('key'))
    op.create_table('admin_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('target', sa.String(length=100), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id'))
    op.create_index('ix_admin_log_user_id', 'admin_log', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_admin_log_user_id', table_name='admin_log')
    op.drop_table('admin_log')
    op.drop_table('app_setting')
    op.drop_index('ix_call_share_to_user_id', table_name='call_share')
    op.drop_index('ix_call_share_call_id', table_name='call_share')
    op.drop_table('call_share')
    op.drop_index('ix_call_followup_owner_ext', table_name='call_followup')
    op.drop_table('call_followup')
    op.drop_column('call', 'content_deleted_at')
