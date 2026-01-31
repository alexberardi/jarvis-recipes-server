#!/bin/bash
# Production server (pulls pre-built images from GHCR)
# Usage: ./run-prod.sh
#
# Prerequisites:
#   1. Create shared network: docker network create microservices
#   2. Run shared postgres (see POSTGRES_SETUP.md)
#   3. Configure .env with DATABASE_URL and other required vars

set -e
cd "$(dirname "$0")"

docker compose --env-file .env -f docker-compose.prod.yaml up -d

echo "jarvis-recipes-server running in production mode"
echo "Logs: docker compose -f docker-compose.prod.yaml logs -f"
echo "Stop: docker compose -f docker-compose.prod.yaml down"
