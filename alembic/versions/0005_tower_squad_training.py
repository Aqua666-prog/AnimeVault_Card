"""Spendable training experience, three-slot squad, and an endless tower.

Backfills training XP from lifetime XP without modifying existing balances or cards.
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_tower_squad_training"
down_revision = "0004_card_progression"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "wallets",
        sa.Column("training_xp", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE wallets SET training_xp = xp")
    op.create_check_constraint("ck_wallet_training_xp", "wallets", "training_xp >= 0")
    op.add_column(
        "card_upgrades",
        sa.Column("xp_cost", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint("ck_card_upgrades_xp_cost", "card_upgrades", "xp_cost >= 0")
    op.create_table(
        "squad_slots",
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Uuid(), sa.ForeignKey("owned_cards.id"), nullable=False),
        sa.CheckConstraint("slot BETWEEN 1 AND 3", name="ck_squad_slot"),
        sa.UniqueConstraint("card_id", name="uq_squad_card"),
    )
    op.create_table(
        "tower_progress",
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), primary_key=True),
        sa.Column("highest_cleared", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("highest_cleared >= 0", name="ck_tower_progress_floor"),
    )
    op.create_table(
        "tower_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("key", sa.String(16), nullable=False),
        sa.Column("floor", sa.BigInteger(), nullable=False),
        sa.Column("victory", sa.Boolean(), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("remaining_hp", sa.BigInteger(), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("player_id", "key", name="uq_tower_runs_player_key"),
        sa.CheckConstraint("floor >= 1", name="ck_tower_runs_floor"),
        sa.CheckConstraint("turns BETWEEN 1 AND 60", name="ck_tower_runs_turns"),
        sa.CheckConstraint("coins >= 0 AND xp >= 0", name="ck_tower_runs_rewards"),
    )
    op.create_index("ix_tower_runs_player_created", "tower_runs", ["player_id", "created_at"])


def downgrade():
    op.drop_index("ix_tower_runs_player_created", table_name="tower_runs")
    op.drop_table("tower_runs")
    op.drop_table("tower_progress")
    op.drop_table("squad_slots")
    op.drop_constraint("ck_card_upgrades_xp_cost", "card_upgrades", type_="check")
    op.drop_column("card_upgrades", "xp_cost")
    op.drop_constraint("ck_wallet_training_xp", "wallets", type_="check")
    op.drop_column("wallets", "training_xp")
