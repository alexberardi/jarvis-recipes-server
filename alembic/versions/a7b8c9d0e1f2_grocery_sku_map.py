"""Household-owned ingredient -> retailer SKU map.

Recipes owns this map rather than deferring to jarvis-pantry: the thing being
mapped is "what this household buys when a recipe says 'ground beef'", which is
a shopping preference, not inventory. Keeping it here means the export works
with no cross-service dependency, and the map is the household's to correct.

Scoped like recipes and meal_plans: `user_id` is authorship, `household_id` is
visibility. Nullable household so a user who belongs to none still gets a map.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "grocery_sku_map",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False, index=True),
        sa.Column("household_id", sa.String(255), nullable=True, index=True),
        # Retailer is a column, not a table, because the only thing that differs
        # between them is the id format and the cart URL. A second retailer costs
        # a row here and a URL builder, not a schema change.
        sa.Column("retailer", sa.String(32), nullable=False, server_default="walmart"),
        # The NORMALIZED ingredient name, as shopping_list_service produces it --
        # "beef sirloin", not "1 1/2 lb beef sirloin, sliced thin". Matching on
        # the raw line would need a new row for every recipe's phrasing.
        sa.Column("ingredient_name", sa.String(255), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(512), nullable=True),
        sa.Column("unit_size", sa.String(64), nullable=True),
        # "manual" (chosen in the picker) or "llm" (matched to an existing entry
        # by the background pass). Manual must win: it is the household saying
        # what they actually buy, and an LLM guess should never overwrite it.
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    # One mapping per ingredient per household per retailer. Without this the
    # LLM pass and the picker race to insert the same row and the cart gets the
    # item twice.
    op.create_index(
        "ux_grocery_sku_map_scope",
        "grocery_sku_map",
        ["household_id", "user_id", "retailer", "ingredient_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_grocery_sku_map_scope", table_name="grocery_sku_map")
    op.drop_table("grocery_sku_map")
