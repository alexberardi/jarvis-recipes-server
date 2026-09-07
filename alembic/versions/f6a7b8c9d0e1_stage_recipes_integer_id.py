"""Give stage_recipes an integer id like every other recipe id

stage_recipes.id was a UUID string while recipes.id is an integer, and the two
flow through the same fields -- Selection.recipe_id, the mobile plan state, the
exclusion lists sent back to the API. That mismatch caused three separate bugs:

  * drilling into a staged pick called /recipes/<uuid> and got a 422
  * re-roll and shuffle sent Number(uuid) -> NaN -> JSON null in
    exclude_recipe_ids: List[int], which the API rejected with a 422, breaking
    both actions for the whole plan
  * meal_plan_items.recipe_id is an FK to recipes.id (Integer), so a plan holding
    a staged pick could not be committed at all

Dropping and recreating rather than migrating: nothing has a foreign key to this
table, its rows are short-lived by design (cleanup_expired_stage_recipes prunes
them on a timer), and the service is not deployed anywhere. Converting UUIDs to
integers in place would be more code for data that is meant to evaporate.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create(id_column: sa.Column) -> None:
    op.create_table(
        "stage_recipes",
        id_column,
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("yield_text", sa.String()),
        sa.Column("prep_time_minutes", sa.Integer(), default=0),
        sa.Column("cook_time_minutes", sa.Integer(), default=0),
        sa.Column("ingredients", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stage_recipes_id", "stage_recipes", ["id"])
    op.create_index("ix_stage_recipes_user_id", "stage_recipes", ["user_id"])
    op.create_index("ix_stage_recipes_request_id", "stage_recipes", ["request_id"])


def upgrade() -> None:
    op.drop_table("stage_recipes")
    _create(sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True))


def downgrade() -> None:
    op.drop_table("stage_recipes")
    _create(sa.Column("id", sa.String(), primary_key=True))
