"""Prune phantom settings and seed the real runtime knobs

The three ``parser.*`` rows seeded by b2c3d4e5f6g7 had no reader anywhere in the
service — jarvis-admin showed them as editable and changing them did nothing.
They are deleted here. In their place we seed the knobs that ARE read at runtime
(model names, queue retries, the parse-job abandon window, the upload size cap
and the scraper User-Agent), each with the env var it used to come from as its
env_fallback so an unseeded/overridden deployment behaves exactly as before.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6g7
Create Date: 2026-08-23 14:00:00.000000
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The rows b2c3d4e5f6g7 seeded for keys with no consumer. Dropped, not re-seeded;
# kept in full so downgrade() can put them back.
PHANTOM_SETTINGS: list[dict[str, Any]] = [
    {
        "key": "parser.timeout_seconds",
        "value": "30",
        "value_type": "int",
        "category": "parser",
        "description": "Timeout for recipe URL parsing in seconds",
        "env_fallback": "PARSER_TIMEOUT",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "parser.max_retries",
        "value": "3",
        "value_type": "int",
        "category": "parser",
        "description": "Maximum retries for recipe URL parsing",
        "env_fallback": "PARSER_MAX_RETRIES",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "parser.use_llm_fallback",
        "value": "true",
        "value_type": "bool",
        "category": "parser",
        "description": "Whether to use LLM as fallback for recipe parsing",
        "env_fallback": "PARSER_USE_LLM_FALLBACK",
        "requires_reload": False,
        "is_secret": False,
    },
]

# Values mirror the defaults in jarvis_recipes/app/services/settings_service.py.
# tests/test_settings.py asserts the two stay in sync.
NEW_SETTINGS: list[dict[str, Any]] = [
    {
        "key": "llm.full_model_name",
        "value": "live",
        "value_type": "string",
        "category": "llm",
        "description": "Model name used for full recipe extraction and meal planning",
        "env_fallback": "JARVIS_FULL_MODEL_NAME",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "llm.lightweight_model_name",
        "value": "live",
        "value_type": "string",
        "category": "llm",
        "description": "Model name used for cheap OCR-text structuring passes",
        "env_fallback": "JARVIS_LIGHTWEIGHT_MODEL_NAME",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "queue.max_retries",
        "value": "3",
        "value_type": "int",
        "category": "queue",
        "description": "Maximum retries for a failed recipe parse job",
        "env_fallback": "LLM_RECIPE_QUEUE_MAX_RETRIES",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "parse_job.abandon_minutes",
        "value": "4320",
        "value_type": "int",
        "category": "parse_job",
        "description": "Minutes before an in-progress parse job is considered abandoned",
        "env_fallback": "RECIPE_PARSE_JOB_ABANDON_MINUTES",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "image.max_bytes",
        "value": str(10 * 1024 * 1024),
        "value_type": "int",
        "category": "image",
        "description": "Maximum accepted size of a single uploaded recipe image, in bytes",
        "env_fallback": "RECIPE_IMAGE_MAX_BYTES",
        "requires_reload": False,
        "is_secret": False,
    },
    {
        "key": "scraper.user_agent",
        "value": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "value_type": "string",
        "category": "scraper",
        "description": "User-Agent header sent when fetching recipe pages",
        "env_fallback": "SCRAPER_USER_AGENT",
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

# Only the system-default rows are touched; per-household/node/user overrides an
# operator created for these keys are theirs to remove.
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

    for setting in PHANTOM_SETTINGS:
        conn.execute(_DELETE, {"key": setting["key"]})

    for setting in NEW_SETTINGS:
        conn.execute(insert, setting)


def downgrade() -> None:
    conn = op.get_bind()
    insert = _INSERT_POSTGRES if conn.dialect.name == "postgresql" else _INSERT_SQLITE

    for setting in NEW_SETTINGS:
        conn.execute(_DELETE, {"key": setting["key"]})

    # Restore the phantom rows so the downgrade lands back on b2c3d4e5f6g7's state.
    for setting in PHANTOM_SETTINGS:
        conn.execute(insert, setting)
