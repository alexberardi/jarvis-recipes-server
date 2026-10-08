"""Seed ocr.transport

Photo imports used to reach OCR by LPUSHing an envelope onto Redis
`jarvis.ocr.jobs`, with the Python OCR worker enqueueing a pickled
`ocr.completed` job back onto ours. jarvisd absorbed OCR and has no Redis, so the
hand-off is now HTTP: POST /v1/ocr/jobs with a callback_url. `ocr.transport`
picks between the two -- "http" (jarvisd, the default) or "redis" (a legacy
stack that has not cut over yet).

Prune-shaped (PHANTOM_SETTINGS / NEW_SETTINGS) so tests/test_settings.py can fold
it into what a freshly migrated database contains. Nothing is pruned here.

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-10-08 12:00:00.000000
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PHANTOM_SETTINGS: list[dict[str, Any]] = []

NEW_SETTINGS: list[dict[str, Any]] = [
    {
        "key": "ocr.transport",
        "value": "http",
        "value_type": "string",
        "category": "ocr",
        "description": "How photo imports reach OCR: 'http' (jarvisd job API + callback) or 'redis' (legacy jarvis-ocr-service queue)",
        "env_fallback": "RECIPES_OCR_TRANSPORT",
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
