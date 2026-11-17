"""the forward chain (spec 2026-11-16-priority-no-followup, "Part 4 change"): table forward_hop (one row per person
who held the problem: level 1 employee / 2 branch supervisor / 3 manager; the solution text is personal data, erased
with the call's content); forward_person gets is_supervisor / is_manager and loses the (branch, name) unique key,
because every employee of the other system is stored now (two people may share a name in one branch).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('forward_person', sa.Column('is_supervisor', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('forward_person', sa.Column('is_manager', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('forward_person', sa.Column('grp', sa.String(length=20), nullable=True))   # men / women there
    op.drop_constraint('uq_forward_person_branch_name', 'forward_person', type_='unique')
    op.create_table(
        'forward_hop',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('call_id', sa.BigInteger(), nullable=False),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('person_id', sa.Integer(), nullable=False),
        sa.Column('ticket_guid', sa.String(length=64), nullable=True),
        sa.Column('ticket_number', sa.Integer(), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('solution', sa.String(length=1000), nullable=True),
        sa.Column('result', sa.String(length=20), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.String(length=300), nullable=True),
        sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['call_id'], ['call.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['person_id'], ['forward_person.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('level IN (1, 2, 3)', name='ck_forward_hop_level'),
        sa.CheckConstraint("result IN ('pending', 'open', 'solved', 'passed_on', 'closed_by_other', 'failed')",
                           name='ck_forward_hop_result'),
    )
    op.create_index('ix_forward_hop_call_id', 'forward_hop', ['call_id'])
    op.create_index('ix_forward_hop_result', 'forward_hop', ['result'])
    # which hop (person) held the ticket when a timeline item was first read: the supervisors' story page
    op.add_column('forward_event', sa.Column('hop_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key('fk_forward_event_hop', 'forward_event', 'forward_hop', ['hop_id'], ['id'],
                          ondelete='SET NULL')
    # forwards sent before the chain (D1/D2: always to a branch supervisor) get their level-2 hop, so they are
    # still read back, checked and erased with the call (review DC)
    op.execute("""
        INSERT INTO forward_hop (call_id, level, person_id, ticket_guid, ticket_number, sent_at, closed_at, solution,
                                 result, checked_at)
        SELECT call_id, 2, forward_person_id, forward_ticket_guid, forward_ticket_number, forwarded_at,
               forward_closed_at, forward_solution,
               CASE WHEN forward_closed_at IS NULL THEN 'open' ELSE 'solved' END, forward_checked_at
        FROM call_followup
        WHERE forward_status = 'sent' AND forward_person_id IS NOT NULL AND forward_ticket_guid IS NOT NULL""")
    op.execute("UPDATE forward_person SET is_supervisor = true WHERE ext IS NOT NULL")


def downgrade() -> None:
    op.drop_constraint('fk_forward_event_hop', 'forward_event', type_='foreignkey')
    op.drop_column('forward_event', 'hop_id')
    op.drop_index('ix_forward_hop_result', table_name='forward_hop')
    op.drop_index('ix_forward_hop_call_id', table_name='forward_hop')
    op.drop_table('forward_hop')
    op.create_unique_constraint('uq_forward_person_branch_name', 'forward_person', ['branch', 'name'])
    op.drop_column('forward_person', 'grp')
    op.drop_column('forward_person', 'is_manager')
    op.drop_column('forward_person', 'is_supervisor')
