"""rule_list + rule_change: the admin edits word lists and topics on a page (spec 2026-11-17-rules-editor)

The lists are filled here once from config/rules.yaml + config/topics.yaml (one history row each); after that the
DB wins. If the files cannot be read the tables stay empty and the app keeps using the YAML until the page is opened
(it fills the missing lists then). Add-only.
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_list",
        sa.Column("name", sa.String(30), primary_key=True),
        sa.Column("items", JSONB(), nullable=False),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "rule_change",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("list_name", sa.String(30), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("added", JSONB(), nullable=False),
        sa.Column("removed", JSONB(), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_rule_change_list_name", "rule_change", ["list_name"])
    _seed()


def _seed() -> None:
    try:
        from callsight.analysis.rulestore import TOPICS, paths, yaml_lists
        lists = yaml_lists(*paths())
    except Exception as exc:  # noqa: BLE001 - never block the deploy; the page fills the lists on first open
        print(f"rule lists not filled from YAML ({type(exc).__name__}); the page fills them on first open")
        return
    now = datetime.now(timezone.utc)
    rl = sa.table("rule_list", sa.column("name"), sa.column("items", JSONB()), sa.column("updated_at"))
    rc = sa.table("rule_change", sa.column("list_name"), sa.column("added", JSONB()), sa.column("removed", JSONB()),
                  sa.column("reason"), sa.column("at"))
    op.bulk_insert(rl, [{"name": n, "items": items, "updated_at": now} for n, items in lists.items()])
    op.bulk_insert(rc, [{"list_name": n, "added": [f"{k}: {v}" for k, v in items] if n == TOPICS else items,
                         "removed": [], "reason": "first fill from the YAML file", "at": now}
                        for n, items in lists.items()])


def downgrade() -> None:
    op.drop_index("ix_rule_change_list_name", table_name="rule_change")
    op.drop_table("rule_change")
    op.drop_table("rule_list")
