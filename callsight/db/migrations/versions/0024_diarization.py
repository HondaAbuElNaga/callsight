"""Diarization (spec 2026-11-17-diarization): word times per window, pyannote turns, agent / customer lines,
talk metrics per transcript. Add-only; old transcripts stay as they are (NULL = not split yet)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None

METRICS = (("speaker_unsure", sa.Boolean()), ("speaker_reason", sa.String(20)), ("agent_talk_s", sa.Float()),
           ("customer_talk_s", sa.Float()), ("talk_ratio", sa.Float()), ("interruptions_agent", sa.Integer()),
           ("interruptions_customer", sa.Integer()), ("longest_wait_s", sa.Float()))


def upgrade() -> None:
    op.add_column("transcript_segment", sa.Column("words", JSONB(), nullable=True))
    for name, type_ in METRICS:
        op.add_column("transcript", sa.Column(name, type_, nullable=True))
    op.create_table(
        "speaker_turn",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transcript_id", sa.BigInteger(), sa.ForeignKey("transcript.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker_raw", sa.String(20), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
    )
    op.create_index("ix_speaker_turn_transcript_id", "speaker_turn", ["transcript_id"])
    op.create_table(
        "transcript_turn",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("transcript_id", sa.BigInteger(), sa.ForeignKey("transcript.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(10), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("segment_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_transcript_turn_transcript_id", "transcript_turn", ["transcript_id"])


def downgrade() -> None:
    op.drop_index("ix_transcript_turn_transcript_id", table_name="transcript_turn")
    op.drop_table("transcript_turn")
    op.drop_index("ix_speaker_turn_transcript_id", table_name="speaker_turn")
    op.drop_table("speaker_turn")
    for name, _ in reversed(METRICS):
        op.drop_column("transcript", name)
    op.drop_column("transcript_segment", "words")
