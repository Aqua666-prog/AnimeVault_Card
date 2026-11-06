"""Durable rewards, UTC visits, opt-in journey and combat snapshots; no progress reset."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0006_gameplay_ui_v2"
down_revision = "0005_tower_squad_training"
branch_labels = None
depends_on = None


def upgrade():
    for table, columns in (
        ("wallets", ("balance", "xp", "training_xp")),
        ("currency_transactions", ("delta", "balance_after")),
        ("summons", ("balance_after",)),
    ):
        for column in columns:
            op.alter_column(table, column, type_=sa.BigInteger(), existing_type=sa.Integer())
    op.drop_constraint("ck_card_upgrades_steps", "card_upgrades", type_="check")
    op.create_check_constraint("ck_card_upgrades_steps", "card_upgrades", "steps BETWEEN 1 AND 99")
    op.add_column("daily_rewards", sa.Column("telegram_update_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_daily_player_update", "daily_rewards", ["player_id", "telegram_update_id"]
    )
    op.add_column(
        "tower_runs",
        sa.Column("first_clear", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("tower_runs", sa.Column("report", JSONB(), nullable=True))
    op.execute("""UPDATE tower_runs SET first_clear = true WHERE id IN (
        SELECT DISTINCT ON (player_id,floor) id FROM tower_runs WHERE victory
        AND (coins = least(100000::numeric,80+25*floor::numeric)
             OR (coins = least(400::numeric,80+25*floor::numeric)
                 AND xp = least(300::numeric,35+15*floor::numeric)))
        ORDER BY player_id,floor,created_at,id)""")
    op.create_table(
        "reward_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(250), nullable=False),
        sa.Column("coins", sa.BigInteger(), nullable=False),
        sa.Column("xp", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("player_id", "key", name="uq_reward_player_key"),
        sa.CheckConstraint("coins >= 0 AND xp >= 0", name="ck_reward_amounts"),
    )
    op.create_index("ix_reward_grants_player_id", "reward_grants", ["player_id"])
    op.create_table(
        "player_visits",
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
    )
    op.create_table(
        "player_journeys",
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("tutorial_skipped", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("viewed_card", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("viewed_tasks", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("INSERT INTO player_journeys (player_id) SELECT id FROM players")


def downgrade():
    # Only isolated integration databases may discard the new receipt tables.
    if not (op.get_bind().engine.url.database or "").endswith("_test"):
        raise RuntimeError("Reward receipts must be retained; use a forward migration")
    op.drop_constraint("uq_daily_player_update", "daily_rewards", type_="unique")
    op.drop_column("daily_rewards", "telegram_update_id")
    op.drop_table("player_journeys")
    op.drop_table("player_visits")
    op.drop_index("ix_reward_grants_player_id", table_name="reward_grants")
    op.drop_table("reward_grants")
    op.drop_column("tower_runs", "report")
    op.drop_column("tower_runs", "first_clear")
    # Keep widened resource columns: narrowing could destroy accumulated values.
