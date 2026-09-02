"""Drop the auth.algorithm setting — verification no longer consults it

Same situation as c3d4e5f6a7b8's phantom rows: a knob jarvis-admin renders as
editable that no longer changes anything.

`auth.algorithm` only ever gated JWT *verification* in this service, and the
HS256 -> RS256 migration makes a single configured algorithm precisely the wrong
control: a verifier has to accept BOTH during the transition, or tokens minted
before the flip stop working the moment jarvis-auth switches. Verification now
takes the algorithm per token from an explicit allowlist instead. What gets
MINTED is jarvis-auth's setting; this service only decides what it accepts.

See prds/auth-rs256-migration.md.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-23 23:20:00.000000
"""

from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Kept in full so downgrade() can restore the row exactly as b2c3d4e5f6g7 left it.
PHANTOM_SETTINGS: list[dict[str, Any]] = [
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
]

# Nothing new to seed here.
NEW_SETTINGS: list[dict[str, Any]] = []


_INSERT_POSTGRES = sa.text(
    """
    INSERT INTO settings (key, value, value_type, category, description,
                          env_fallback, requires_reload, is_secret,
                          household_id, node_id, user_id)
    VALUES (:key, :value, :value_type, :category, :description,
            :env_fallback, :requires_reload, :is_secret,
            NULL, NULL, NULL)
    ON CONFLICT DO NOTHING
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
    for setting in PHANTOM_SETTINGS:
        conn.execute(_DELETE, {"key": setting["key"]})


def downgrade() -> None:
    conn = op.get_bind()
    insert = _INSERT_POSTGRES if conn.dialect.name == "postgresql" else _INSERT_SQLITE
    for setting in PHANTOM_SETTINGS:
        conn.execute(insert, setting)
