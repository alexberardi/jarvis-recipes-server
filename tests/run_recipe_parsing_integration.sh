#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

docker compose run --rm recipes-api bash -lc "cd /app && poetry install --with dev && poetry run pytest tests/test_recipe_parsing_integration.py $*"

