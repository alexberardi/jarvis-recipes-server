#!/bin/bash
# Development server with hot reload
# Usage: ./run.sh [--build]

set -e
cd "$(dirname "$0")"

BUILD_FLAGS=""
if [[ "$1" == "--rebuild" ]]; then
    docker compose --env-file .env -f docker-compose.dev.yaml build --no-cache 
    BUILD_FLAGS="--build"
elif [[ "$1" == "--build" ]]; then
    BUILD_FLAGS="--build"
fi

docker compose --env-file .env -f docker-compose.dev.yaml up --force-recreate $BUILD_FLAGS
