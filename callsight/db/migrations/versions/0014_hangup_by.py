"""who ended an outbound call: call.hangup_by (agent / customer / busy), back-filled from call_leg.reason.
Spec 2026-11-15-ring-time. Additive only.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('call', sa.Column('hangup_by', sa.String(length=10), nullable=True))
    # same rule as classify.hangup_by(): answered leg first, else the last ringing (extension/external) leg
    op.execute("""
        UPDATE call c SET hangup_by = CASE
            WHEN c.status = 'busy' THEN 'busy'
            WHEN lower(btrim(l.reason)) LIKE 'src%' THEN 'agent'
            WHEN lower(btrim(l.reason)) LIKE 'dst%busy%' THEN 'busy'
            WHEN lower(btrim(l.reason)) LIKE 'dst%' THEN 'customer'
        END
        FROM (SELECT DISTINCT ON (call_id) call_id, reason FROM call_leg
              WHERE kind IN ('extension', 'external')
              ORDER BY call_id, (upper(coalesce(disposition, '')) = 'ANSWERED') DESC,
                       CASE WHEN upper(coalesce(disposition, '')) = 'ANSWERED' THEN id ELSE -id END) l
        WHERE l.call_id = c.id AND c.direction = 'outbound'
    """)


def downgrade() -> None:
    op.drop_column('call', 'hangup_by')
