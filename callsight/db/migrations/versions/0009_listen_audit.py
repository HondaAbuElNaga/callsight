"""live board (spec 2026-11-14-live-board): append-only listen_audit table. Additive only.

Triggers refuse UPDATE / DELETE and a TRUNCATE of a non-empty table (unless a maintenance session
sets callsight.audit_maintenance=on). They stop mistakes, not someone with the DB owner password:
real tamper resistance needs an API DB role with INSERT/SELECT only (later idea).

Before running on the live DB: backup first (python -m callsight backup).

Revision ID: 0009
Revises: 0007
Create Date: 2026-11-14
"""
from alembic import op
import sqlalchemy as sa


# Same statements as callsight.db.models.LISTEN_AUDIT_APPEND_ONLY (copied: a migration must not change later).
LISTEN_AUDIT_APPEND_ONLY = (
    "CREATE OR REPLACE FUNCTION listen_audit_append_only() RETURNS trigger AS $$ BEGIN "
    "RAISE EXCEPTION 'listen_audit is append-only'; END; $$ LANGUAGE plpgsql",
    "CREATE TRIGGER listen_audit_no_change BEFORE UPDATE OR DELETE ON listen_audit "
    "FOR EACH ROW EXECUTE FUNCTION listen_audit_append_only()",
    "CREATE OR REPLACE FUNCTION listen_audit_no_truncate() RETURNS trigger AS $$ BEGIN "
    "IF EXISTS (SELECT 1 FROM listen_audit) AND "
    "coalesce(current_setting('callsight.audit_maintenance', true), '') <> 'on' THEN "
    "RAISE EXCEPTION 'listen_audit is append-only (TRUNCATE refused)'; END IF; RETURN NULL; END; $$ "
    "LANGUAGE plpgsql",
    "CREATE TRIGGER listen_audit_no_truncate BEFORE TRUNCATE ON listen_audit "
    "FOR EACH STATEMENT EXECUTE FUNCTION listen_audit_no_truncate()",
)

revision = '0009'
down_revision = '0007'  # merged before the call-analysis spec, whose 0008 then follows this 0009
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('listen_audit',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('result', sa.String(length=20), nullable=False),
    sa.Column('attempt_id', sa.BigInteger(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('user_ext', sa.String(length=20), nullable=True),
    sa.Column('client_ip', sa.String(length=64), nullable=True),
    sa.Column('live_call_id', sa.String(length=64), nullable=False),
    sa.Column('agent_ext', sa.String(length=20), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['attempt_id'], ['listen_audit.id']),
    sa.ForeignKeyConstraint(['user_id'], ['app_user.id']),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_listen_audit_user_id', 'listen_audit', ['user_id'], unique=False)
    for sql in LISTEN_AUDIT_APPEND_ONLY:
        op.execute(sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS listen_audit_no_change ON listen_audit")
    op.execute("DROP TRIGGER IF EXISTS listen_audit_no_truncate ON listen_audit")
    op.drop_index('ix_listen_audit_user_id', table_name='listen_audit')
    op.drop_table('listen_audit')
    op.execute("DROP FUNCTION IF EXISTS listen_audit_append_only()")
    op.execute("DROP FUNCTION IF EXISTS listen_audit_no_truncate()")
