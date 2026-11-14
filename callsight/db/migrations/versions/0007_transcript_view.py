"""transcript view (spec 2026-11-14-transcript-view): transcript.search_text + pg_trgm GIN index,
transcript_access audit table, batched backfill of search_text. Additive only.

Before running on the live DB: pg_dump first (backup). `CREATE EXTENSION pg_trgm` needs the DB owner (callsight).

Revision ID: 0007
Revises: 0005
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0005'  # down_revision becomes "0006" when merged after the hardening spec
branch_labels = None
depends_on = None

BATCH = 200


def backfill(conn) -> int:
    """search_text for every done transcript that has none yet, in batches (ids only in memory between batches)."""
    from callsight.transcripts import search_normalize
    n, last = 0, 0
    while True:
        ids = [r[0] for r in conn.execute(sa.text(
            "SELECT id FROM transcript WHERE status = 'done' AND search_text IS NULL AND id > :last "
            "ORDER BY id LIMIT :n"), {"last": last, "n": BATCH})]
        if not ids:
            return n
        for tid in ids:
            texts = conn.execute(sa.text(
                "SELECT text FROM transcript_segment WHERE transcript_id = :t AND flag IS NULL ORDER BY seq"),
                {"t": tid}).scalars()
            value = " ".join(t for t in (search_normalize(x) for x in texts if x) if t)
            conn.execute(sa.text("UPDATE transcript SET search_text = :v WHERE id = :t"), {"v": value, "t": tid})
        n += len(ids)
        last = ids[-1]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.add_column('transcript', sa.Column('search_text', sa.Text(), nullable=True))
    op.create_table('transcript_access',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('call_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('accessed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_transcript_access_user_id', 'transcript_access', ['user_id'], unique=False)
    op.create_index('ix_transcript_access_call_id', 'transcript_access', ['call_id'], unique=False)
    backfill(op.get_bind())
    op.create_index('ix_transcript_search_trgm', 'transcript', ['search_text'], unique=False,
                    postgresql_using='gin', postgresql_ops={'search_text': 'gin_trgm_ops'})


def downgrade() -> None:
    op.drop_index('ix_transcript_search_trgm', table_name='transcript')
    op.drop_index('ix_transcript_access_call_id', table_name='transcript_access')
    op.drop_index('ix_transcript_access_user_id', table_name='transcript_access')
    op.drop_table('transcript_access')
    op.drop_column('transcript', 'search_text')
    # pg_trgm stays installed (harmless; other objects may use it)
