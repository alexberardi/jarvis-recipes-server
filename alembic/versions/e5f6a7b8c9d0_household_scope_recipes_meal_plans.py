"""Scope recipes and meal plans to the household

A recipe box is a household's, not a person's. "What's for dinner tonight" asked
at a kitchen node is a household question, and a family meal planner where each
member only sees recipes they personally imported is the wrong product.

Adds household_id alongside user_id rather than replacing it: user_id stays as
authorship ("added by Casey"), household_id becomes visibility. That also makes
this reversible -- downgrade drops the column and per-user scoping returns
exactly as it was.

NULLABLE, and NOT backfilled here. This service cannot resolve a user's
household; that mapping lives in jarvis-auth, and a migration has no business
making network calls. scripts/backfill_household_ids.py does it afterwards.
Until then services/scoping.py keeps NULL rows visible to their author, so
deploying this alone makes nothing disappear.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# recipe_parse_jobs is included because the queue worker has no token: it plans
# and imports on behalf of a user long after the request that started it. The
# household has to travel on the job row or the worker cannot tell which box the
# result belongs in.
_TABLES = ("recipes", "meal_plans", "recipe_parse_jobs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("household_id", sa.String(255), nullable=True))
        # Every read filters on this, so it is indexed from the start rather than
        # after the first slow query.
        op.create_index(f"ix_{table}_household_id", table, ["household_id"])


def downgrade() -> None:
    for table in _TABLES:
        op.drop_index(f"ix_{table}_household_id", table_name=table)
        op.drop_column(table, "household_id")
