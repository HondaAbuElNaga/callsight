"""Shared calls become a chat (spec 2026-11-25-share-chat): call_share.status (open / archived / closed),
archived_at, closed_at, agent_read_at, staff_read_at, and table share_message. Add-only. Existing notes and replies
are copied into messages. Shares the agent had closed become archived. The staff read mark starts now, so old
replies don't pop up.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-25
"""
from alembic import op
import sqlalchemy as sa

revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_share", sa.Column("status", sa.String(10), nullable=False, server_default="open"))
    op.add_column("call_share", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_share", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_share", sa.Column("agent_read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_share", sa.Column("staff_read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_call_share_status", "call_share", ["status"])
    op.create_table(
        "share_message",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("share_id", sa.BigInteger, sa.ForeignKey("call_share.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("text", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_share_message_share_id", "share_message", ["share_id"])
    op.execute("""
        INSERT INTO share_message (share_id, user_id, text, created_at)
        SELECT id, from_user_id, note, created_at FROM call_share""")
    op.execute("""
        INSERT INTO share_message (share_id, user_id, text, created_at)
        SELECT id, to_user_id, reply, reply_at FROM call_share WHERE reply IS NOT NULL AND reply_at IS NOT NULL""")
    op.execute("""
        UPDATE call_share SET status = 'archived', archived_at = agent_closed_at
        WHERE agent_closed_at IS NOT NULL""")
    op.execute("UPDATE call_share SET agent_read_at = COALESCE(reply_at, seen_at), staff_read_at = now()")


def downgrade() -> None:
    op.drop_index("ix_share_message_share_id", table_name="share_message")
    op.drop_table("share_message")
    op.drop_index("ix_call_share_status", table_name="call_share")
    for col in ("staff_read_at", "agent_read_at", "closed_at", "archived_at", "status"):
        op.drop_column("call_share", col)
