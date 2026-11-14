"""kpi reports: repeat-call indexes, report archive + download audit (additive only)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_call_customer_start', 'call', ['customer_number', 'start_time'])
    op.execute("CREATE INDEX ix_call_norm_number ON call "
               "(right(regexp_replace(customer_number, '[^0-9]', '', 'g'), 9), start_time)")
    op.create_table('report_archive',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('period_type', sa.String(length=10), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('scope', sa.String(length=60), nullable=False),
    sa.Column('file', sa.String(length=300), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('period_type', 'period_start', 'scope', name='uq_report_archive_period')
    )
    op.create_table('report_download',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('archive_id', sa.Integer(), nullable=True),
    sa.Column('period_type', sa.String(length=10), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('scope', sa.String(length=60), nullable=False),
    sa.Column('downloaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['archive_id'], ['report_archive.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_report_download_user_id', 'report_download', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_report_download_user_id', table_name='report_download')
    op.drop_table('report_download')
    op.drop_table('report_archive')
    op.execute("DROP INDEX IF EXISTS ix_call_norm_number")
    op.drop_index('ix_call_customer_start', table_name='call')
