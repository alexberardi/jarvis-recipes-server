"""Seed default settings

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-05 17:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


# Settings definitions from jarvis_recipes/app/services/settings_service.py
# All settings are safe to seed (no secrets or URLs)
SETTINGS = [
    {
        "key": "auth.algorithm",
        "value": "HS256",
        "value_type": "string",
        "category": "auth",
        "description": "JWT signing algorithm",
        "env_fallback": "AUTH_ALGORITHM",
        "requires_reload": False,
        "is_secret": False,
    },
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


def upgrade() -> None:
    conn = op.get_bind()
    is_postgres = conn.dialect.name == 'postgresql'

    for setting in SETTINGS:
        if is_postgres:
            conn.execute(
                sa.text("""
                    INSERT INTO settings (key, value, value_type, category, description,
                                         env_fallback, requires_reload, is_secret,
                                         household_id, node_id, user_id)
                    VALUES (:key, :value, :value_type, :category, :description,
                           :env_fallback, :requires_reload, :is_secret,
                           NULL, NULL, NULL)
                    ON CONFLICT (key, household_id, node_id, user_id) DO NOTHING
                """),
                setting
            )
        else:
            conn.execute(
                sa.text("""
                    INSERT OR IGNORE INTO settings (key, value, value_type, category, description,
                                                   env_fallback, requires_reload, is_secret,
                                                   household_id, node_id, user_id)
                    VALUES (:key, :value, :value_type, :category, :description,
                           :env_fallback, :requires_reload, :is_secret,
                           NULL, NULL, NULL)
                """),
                setting
            )


def downgrade() -> None:
    conn = op.get_bind()
    for setting in SETTINGS:
        conn.execute(
            sa.text("""
                DELETE FROM settings
                WHERE key = :key
                  AND household_id IS NULL
                  AND node_id IS NULL
                  AND user_id IS NULL
            """),
            {"key": setting["key"]}
        )
