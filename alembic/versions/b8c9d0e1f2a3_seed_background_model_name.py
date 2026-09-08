"""Seed llm.background_model_name

Grocery SKU matching runs on the proxy's second model slot. "background" is a
routing alias jarvis-llm-proxy-api accepts directly (api/chat_routes.py), not a
filename -- it selects the slower, larger model that is not holding the
interactive session's context. Nobody is waiting on that pass, and the judgement
it makes ("is buttermilk butter?") is worth the extra seconds.

Prune-shaped (PHANTOM_SETTINGS / NEW_SETTINGS) so tests/test_settings.py can fold
it into what a freshly migrated database contains. Nothing is pruned here.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-06 12:00:00.000000
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PHANTOM_SETTINGS: list[dict[str, Any]] = []

NEW_SETTINGS: list[dict[str, Any]] = [
    {
        "key": "llm.background_model_name",
        "value": "background",
        "value_type": "string",
        "category": "llm",
        "description": "Model name used for slow background passes (grocery SKU matching)",
        "env_fallback": "JARVIS_BACKGROUND_MODEL_NAME",
        "requires_reload": False,
        "is_secret": False,
    },
]

_INSERT_POSTGRES = sa.text(
    """
    INSERT INTO settings (key, value, value_type, category, description,
                          env_fallback, requires_reload, is_secret,
                          household_id, node_id, user_id)
    VALUES (:key, :value, :value_type, :category, :description,
            :env_fallback, :requires_reload, :is_secret,
            NULL, NULL, NULL)
    ON CONFLICT (key, household_id, node_id, user_id) DO NOTHING
    """
)

_INSERT_SQLITE = sa.text(
    """
    INSERT OR IGNORE INTO settings (key, value, value_type, category, description,
                                    env_fallback, requires_reload, is_secret,
                                    household_id, node_id, user_id)
    VALUES (:key, :value, :value_type, :category, :description,
            :env_fallback, :requires_reload, :is_secret,
            NULL, NULL, NULL)
    """
)

# Only the system-default row; per-household/node/user overrides are the
# operator's to remove.
_DELETE = sa.text(
    """
    DELETE FROM settings
    WHERE key = :key
      AND household_id IS NULL
      AND node_id IS NULL
      AND user_id IS NULL
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    insert = _INSERT_POSTGRES if conn.dialect.name == "postgresql" else _INSERT_SQLITE
    for setting in NEW_SETTINGS:
        conn.execute(insert, setting)


def downgrade() -> None:
    conn = op.get_bind()
    for setting in NEW_SETTINGS:
        conn.execute(_DELETE, {"key": setting["key"]})
