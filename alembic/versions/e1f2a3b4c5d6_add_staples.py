"""add staples

Ingredients a household always has in, kept off the shopping list proper and out
of the cart. See db/models.Staple for why `name` holds the normalized
shopping-list key rather than the text the user typed.

Revision ID: e1f2a3b4c5d6
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("household_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_staple_user_name"),
    )
    op.create_index("ix_staples_user_id", "staples", ["user_id"])
    op.create_index("ix_staples_household_id", "staples", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_staples_household_id", table_name="staples")
    op.drop_index("ix_staples_user_id", table_name="staples")
    op.drop_table("staples")
