#!/usr/bin/env bash
set -euo pipefail

# Quick helper to mint a JWT matching the server's expectations.
# Usage:
#   ./scripts/issue_token.sh [user_id] [email]
# The script will load AUTH_SECRET_KEY and AUTH_ALGORITHM from .env if present.

ENV_FILE="${ENV_FILE:-.env}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

SECRET="${AUTH_SECRET_KEY:-change-me}"
ALG="${AUTH_ALGORITHM:-HS256}"
USER_ID="${1:-${AUTH_USER_ID:-1}}"
EMAIL="${2:-${AUTH_USER_EMAIL:-user@example.com}}"

export SECRET ALG USER_ID EMAIL

python - <<'PY'
import os
from jose import jwt

secret = os.environ["SECRET"]
alg = os.environ["ALG"]
user_id = os.environ["USER_ID"]
email = os.environ.get("EMAIL") or None

payload = {"sub": str(user_id)}
if email:
    payload["email"] = email

token = jwt.encode(payload, secret, algorithm=alg)
print(token)
PY

