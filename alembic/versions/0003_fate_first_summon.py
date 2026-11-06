"""Add Fate metadata and immutable operation snapshots without replacing old data."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_fate_first_summon"
down_revision = "54b3f0a493e1"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("code", sa.String(10), nullable=True),
        sa.Column("card_class", sa.String(50), nullable=True),
        sa.Column("collection_name", sa.String(100), nullable=True),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("limited", sa.Boolean(), server_default=sa.false(), nullable=False),
    ):
        op.add_column("characters", column)
    op.create_unique_constraint("uq_characters_code", "characters", ["code"])
    op.add_column("banners", sa.Column("code", sa.String(40), nullable=True))
    op.add_column("banners", sa.Column("drop_rates", postgresql.JSONB(), nullable=True))
    op.create_unique_constraint("uq_banners_code", "banners", ["code"])
    op.add_column("summons", sa.Column("total_cost", sa.Integer(), nullable=True))
    op.add_column("summons", sa.Column("balance_after", sa.Integer(), nullable=True))
    op.add_column("summons", sa.Column("telegram_update_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint(
        "uq_summons_player_update", "summons", ["player_id", "telegram_update_id"]
    )
    op.create_check_constraint("ck_summons_cost", "summons", "total_cost IS NULL OR total_cost > 0")
    op.create_check_constraint(
        "ck_summons_balance", "summons", "balance_after IS NULL OR balance_after >= 0"
    )
    op.create_index("ix_summons_player_created", "summons", ["player_id", "created_at"])
    op.create_index("ix_owned_cards_player_character", "owned_cards", ["player_id", "character_id"])
    # Only new nullable columns are filled; balances/history/results themselves are untouched.
    op.execute("""UPDATE summons s SET total_cost = -t.delta, balance_after = t.balance_after
                  FROM currency_transactions t
                  WHERE t.related_id = s.id::text AND t.player_id = s.player_id
                    AND t.kind = 'summon' AND t.delta < 0""")


def downgrade():
    # Metadata-only rollback: never delete users, catalog rows, owned cards or history.
    op.drop_index("ix_owned_cards_player_character", table_name="owned_cards")
    op.drop_index("ix_summons_player_created", table_name="summons")
    op.drop_constraint("ck_summons_balance", "summons", type_="check")
    op.drop_constraint("ck_summons_cost", "summons", type_="check")
    op.drop_constraint("uq_summons_player_update", "summons", type_="unique")
    for name in ("telegram_update_id", "balance_after", "total_cost"):
        op.drop_column("summons", name)
    op.drop_constraint("uq_banners_code", "banners", type_="unique")
    op.drop_column("banners", "drop_rates")
    op.drop_column("banners", "code")
    op.drop_constraint("uq_characters_code", "characters", type_="unique")
    for name in ("limited", "image_path", "collection_name", "card_class", "code"):
        op.drop_column("characters", name)
