"""transcripts (spec 2026-11-14-transcription): transcript + transcript_segment, additive only

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('transcript',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('model', sa.String(length=50), nullable=False),
    sa.Column('model_revision', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('audio_seconds', sa.Float(), nullable=True),
    sa.Column('speech_seconds', sa.Float(), nullable=True),
    sa.Column('compute_seconds', sa.Float(), nullable=True),
    sa.Column('dropped_windows', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('tries', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('segmenter_version', sa.String(length=30), nullable=True),
    sa.Column('diarization_version', sa.String(length=30), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('queued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('call_id', 'model', name='uq_transcript_call_model')
    )
    op.create_index('ix_transcript_status', 'transcript', ['status'], unique=False)
    op.create_table('transcript_segment',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('transcript_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('start_seconds', sa.Float(), nullable=False),
    sa.Column('end_seconds', sa.Float(), nullable=False),
    sa.Column('speaker', sa.String(length=20), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('flag', sa.String(length=20), nullable=True),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcript.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('transcript_id', 'seq', name='uq_transcript_segment_seq')
    )


def downgrade() -> None:
    op.drop_table('transcript_segment')
    op.drop_index('ix_transcript_status', table_name='transcript')
    op.drop_table('transcript')
