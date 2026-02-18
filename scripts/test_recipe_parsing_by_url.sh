#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 \"https://example.com/recipe\""
  exit 1
fi

RECIPE_URL="$1"
API_BASE="${API_BASE:-http://localhost:7030}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Get a token using default issue_token.sh behavior
TOKEN="$("${SCRIPT_DIR}/issue_token.sh")"

echo "Enqueuing parse job for ${RECIPE_URL} ..."
ENQUEUE_RESP="$(curl -s -X POST "${API_BASE}/recipes/parse-url/async" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${RECIPE_URL}\",\"use_llm_fallback\":true}")"

echo "Enqueue response: ${ENQUEUE_RESP}"

JOB_ID="$(python - <<'PY' "${ENQUEUE_RESP}"
import json,sys
data=json.loads(sys.argv[1])
print(data.get("job_id") or data.get("id") or "")
PY
)"

if [[ -z "${JOB_ID}" ]]; then
  echo "Failed to extract job_id from enqueue response."
  exit 1
fi

echo "Job ID: ${JOB_ID}"
STATUS="PENDING"

while true; do
  RESP="$(curl -s "${API_BASE}/recipes/parse-url/status/${JOB_ID}" \
    -H "Authorization: Bearer ${TOKEN}")"
  echo "Status response: ${RESP}"
  STATUS="$(python - <<'PY' "${RESP}"
import json,sys
data=json.loads(sys.argv[1])
print(data.get("status",""))
PY
)"
  if [[ "${STATUS}" != "PENDING" && "${STATUS}" != "RUNNING" ]]; then
    break
  fi
  sleep 2
done

