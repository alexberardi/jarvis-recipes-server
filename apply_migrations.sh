#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python"

# Apply latest Alembic migrations using Python helper
if [ -x "$VENV_PYTHON" ]; then
    "$VENV_PYTHON" scripts/apply_migrations.py
else
    python3 scripts/apply_migrations.py
fi

