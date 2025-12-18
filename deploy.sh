#!/bin/bash
set -e

# Jarvis Recipes Server - Deployment Script
# Usage: ./deploy.sh [dev|latest|VERSION]

COMPOSE_FILE="docker-compose.staging.yml"
ENV_FILE=".env"
IMAGE_TAG="${1:-dev}"

echo "🚀 Deploying Jarvis Recipes Server"
echo "=================================="
echo "Image tag: $IMAGE_TAG"
echo ""

# Check if .env exists
if [ ! -f "$ENV_FILE" ]; then
    echo "❌ Error: $ENV_FILE not found"
    echo ""
    echo "Please create it from the template:"
    echo "  wget -O .env https://raw.githubusercontent.com/your-username/jarvis-recipes-server/main/env.staging.template"
    echo "  nano .env  # Edit with your values"
    exit 1
fi

# Check if docker-compose exists
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose not found"
    echo "Please install Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

# Export IMAGE_TAG for docker-compose
export IMAGE_TAG

echo "📥 Pulling images..."
docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull

echo ""
echo "🔄 Stopping old containers..."
docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down

echo ""
echo "✨ Starting services..."
docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d

echo ""
echo "⏳ Waiting for services to start..."
sleep 5

echo ""
echo "📊 Service Status:"
docker-compose -f "$COMPOSE_FILE" ps

echo ""
echo "✅ Deployment complete!"
echo ""
echo "View logs:"
echo "  docker-compose -f $COMPOSE_FILE logs -f"
echo ""
echo "Check health:"
echo "  curl http://localhost:\$(grep HOST_PORT .env | cut -d= -f2)/health"
echo ""
echo "Access API docs:"
echo "  http://localhost:\$(grep HOST_PORT .env | cut -d= -f2)/docs"

