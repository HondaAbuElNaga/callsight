"""hardening (spec 2026-11-14-hardening): backup_run, job_run, retention_run + retention_log (additive only)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('backup_run',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('dump_file', sa.String(length=200), nullable=True),
    sa.Column('dump_bytes', sa.BigInteger(), nullable=True),
    sa.Column('counts', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('alembic_version', sa.String(length=50), nullable=True),
    sa.Column('files_copied', sa.Integer(), nullable=False),
    sa.Column('files_bytes', sa.BigInteger(), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('verify_ok', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('job_run',
    sa.Column('job', sa.String(length=50), nullable=False),
    sa.Column('last_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=10), nullable=True),
    sa.PrimaryKeyConstraint('job')
    )
    op.create_table('retention_run',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('ran_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('dry_run', sa.Boolean(), nullable=False),
    sa.Column('days', sa.Integer(), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
    sa.Column('files', sa.Integer(), nullable=False),
    sa.Column('audit_rows', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('retention_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('what', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['retention_run.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_retention_log_run_id', 'retention_log', ['run_id'])
    op.create_index('ix_retention_log_call_id', 'retention_log', ['call_id'])


def downgrade() -> None:
    op.drop_index('ix_retention_log_call_id', table_name='retention_log')
    op.drop_index('ix_retention_log_run_id', table_name='retention_log')
    op.drop_table('retention_log')
    op.drop_table('retention_run')
    op.drop_table('job_run')
    op.drop_table('backup_run')
