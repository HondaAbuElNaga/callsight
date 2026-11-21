"""First-speaker check (diarization spec acceptance): per gold clip, who the admin heard speak first at the start of
the call (agent / customer). Compared with the speaker split's naming; add-only."""
from alembic import op
import sqlalchemy as sa

revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gold_clip", sa.Column("first_speaker", sa.String(10), nullable=True))
    op.add_column("gold_clip", sa.Column("first_speaker_by", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=True))
    op.add_column("gold_clip", sa.Column("first_speaker_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("gold_clip", "first_speaker_at")
    op.drop_column("gold_clip", "first_speaker_by")
    op.drop_column("gold_clip", "first_speaker")
