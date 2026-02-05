"""add settings table

Revision ID: a1b2c3d4e5f6
Revises: 9b030b4470dc
Create Date: 2026-02-05 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "50fe459ab5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("value_type", sa.String(length=50), nullable=False, server_default="string"),
        sa.Column("category", sa.String(length=100), nullable=False, server_default="general"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("requires_reload", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("is_secret", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("env_fallback", sa.String(length=255), nullable=True),
        sa.Column("household_id", sa.String(length=255), nullable=True),
        sa.Column("node_id", sa.String(length=255), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", "household_id", "node_id", "user_id", name="uq_setting_scope"),
    )
    op.create_index(op.f("ix_settings_id"), "settings", ["id"], unique=False)
    op.create_index(op.f("ix_settings_key"), "settings", ["key"], unique=False)
    op.create_index(op.f("ix_settings_category"), "settings", ["category"], unique=False)
    op.create_index(op.f("ix_settings_household_id"), "settings", ["household_id"], unique=False)
    op.create_index(op.f("ix_settings_node_id"), "settings", ["node_id"], unique=False)
    op.create_index(op.f("ix_settings_user_id"), "settings", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_settings_user_id"), table_name="settings")
    op.drop_index(op.f("ix_settings_node_id"), table_name="settings")
    op.drop_index(op.f("ix_settings_household_id"), table_name="settings")
    op.drop_index(op.f("ix_settings_category"), table_name="settings")
    op.drop_index(op.f("ix_settings_key"), table_name="settings")
    op.drop_index(op.f("ix_settings_id"), table_name="settings")
    op.drop_table("settings")
