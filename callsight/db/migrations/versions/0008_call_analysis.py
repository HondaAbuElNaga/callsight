"""call analysis (spec 2026-11-14-call-analysis): call_analysis, call_flag, call_audio_metrics, satisfaction_label,
blind_set. Additive only; every table goes with its call (ON DELETE CASCADE, decision 12).

Before running on the live DB: backup first (python -m callsight backup).

Revision ID: 0008
Revises: 0009
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0008'
down_revision = '0009'  # merged after the live-board spec (0009 came first)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('call_analysis',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('transcript_id', sa.BigInteger(), nullable=True),
    sa.Column('model', sa.String(length=50), nullable=False),
    sa.Column('prompt_version', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('topic', sa.String(length=30), nullable=True),
    sa.Column('action_items', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('llm_score', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('escalation', sa.Boolean(), nullable=True),
    sa.Column('trimmed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('prompt_tokens', sa.Integer(), nullable=True),
    sa.Column('compute_seconds', sa.Float(), nullable=True),
    sa.Column('tries', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'model', 'prompt_version', name='uq_call_analysis_version')
    )
    op.create_index('ix_call_analysis_status', 'call_analysis', ['status'], unique=False)
    op.create_index('ix_call_analysis_call_id', 'call_analysis', ['call_id'], unique=False)
    op.create_table('call_flag',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('transcript_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('phrase', sa.String(length=100), nullable=False),
    sa.Column('start_seconds', sa.Float(), nullable=False),
    sa.Column('end_seconds', sa.Float(), nullable=False),
    sa.Column('rules_version', sa.String(length=30), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_call_flag_call_id', 'call_flag', ['call_id'], unique=False)
    op.create_table('call_audio_metrics',
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('metrics_version', sa.String(length=30), nullable=False),
    sa.Column('audio_seconds', sa.Float(), nullable=False),
    sa.Column('silence_seconds', sa.Float(), nullable=False),
    sa.Column('silence_share', sa.Float(), nullable=False),
    sa.Column('longest_silence_s', sa.Float(), nullable=False),
    sa.Column('silences', sa.Integer(), nullable=False),
    sa.Column('long_silences', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('call_id')
    )
    op.create_table('satisfaction_label',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=500), nullable=True),
    sa.Column('blind', sa.Boolean(), nullable=False),
    sa.Column('prefill', sa.Integer(), nullable=True),
    sa.Column('changed_from_prefill', sa.Boolean(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('score BETWEEN 1 AND 10', name='ck_satisfaction_label_score'),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'user_id', name='uq_satisfaction_label_call_user')
    )
    op.create_index('ix_satisfaction_label_call_id', 'satisfaction_label', ['call_id'], unique=False)
    op.create_index('ix_satisfaction_label_user_id', 'satisfaction_label', ['user_id'], unique=False)
    op.create_table('blind_set',
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('agent_ext', sa.String(length=16), nullable=True),
    sa.Column('sampler_version', sa.String(length=20), nullable=False),
    sa.Column('salt', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('call_id')
    )


def downgrade() -> None:
    op.drop_table('blind_set')
    op.drop_index('ix_satisfaction_label_user_id', table_name='satisfaction_label')
    op.drop_index('ix_satisfaction_label_call_id', table_name='satisfaction_label')
    op.drop_table('satisfaction_label')
    op.drop_table('call_audio_metrics')
    op.drop_index('ix_call_flag_call_id', table_name='call_flag')
    op.drop_table('call_flag')
    op.drop_index('ix_call_analysis_call_id', table_name='call_analysis')
    op.drop_index('ix_call_analysis_status', table_name='call_analysis')
    op.drop_table('call_analysis')
