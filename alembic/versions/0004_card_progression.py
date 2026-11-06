"""Permanent card stats, level 1..100, NP 1..5, reversible fusion accounting.

Existing owned-card rows and summon history are preserved.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_card_progression"
down_revision = "0003_fate_first_summon"
branch_labels = None
depends_on = None


def upgrade():
    for name, default in (("base_hp", 1000), ("base_atk", 100), ("base_def", 100)):
        op.add_column(
            "characters",
            sa.Column(name, sa.Integer(), nullable=False, server_default=str(default)),
        )
    for name, default in (("level", 1), ("np", 1)):
        op.add_column(
            "owned_cards",
            sa.Column(name, sa.Integer(), nullable=False, server_default=str(default)),
        )
    op.add_column("owned_cards", sa.Column("consumed_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_owned_cards_level", "owned_cards", "level BETWEEN 1 AND 100")
    op.create_check_constraint("ck_owned_cards_np", "owned_cards", "np BETWEEN 1 AND 5")
    for code, hp, atk, defense in (
        ("AV-F001", 1600, 120, 120),
        ("AV-F002", 1250, 165, 85),
        ("AV-F003", 1800, 115, 135),
        ("AV-L001", 1450, 185, 100),
    ):
        op.execute(
            f"UPDATE characters SET base_hp={hp}, base_atk={atk}, "
            f"base_def={defense} WHERE code='{code}'"
        )
    op.create_table(
        "card_upgrades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("player_id", sa.Uuid(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("target_card_id", sa.Uuid(), sa.ForeignKey("owned_cards.id"), nullable=False),
        sa.Column("source_card_id", sa.Uuid(), sa.ForeignKey("owned_cards.id")),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("coin_cost", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("player_id", "key", name="uq_card_upgrades_player_key"),
        sa.CheckConstraint("kind IN ('level','np')", name="ck_card_upgrades_kind"),
        sa.CheckConstraint("steps BETWEEN 1 AND 10", name="ck_card_upgrades_steps"),
        sa.CheckConstraint("coin_cost >= 0", name="ck_card_upgrades_cost"),
    )


def downgrade():
    op.drop_table("card_upgrades")
    op.drop_constraint("ck_owned_cards_np", "owned_cards", type_="check")
    op.drop_constraint("ck_owned_cards_level", "owned_cards", type_="check")
    op.drop_column("owned_cards", "consumed_at")
    op.drop_column("owned_cards", "np")
    op.drop_column("owned_cards", "level")
    for name in ("base_def", "base_atk", "base_hp"):
        op.drop_column("characters", name)
