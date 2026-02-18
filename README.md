# Jarvis Recipes API

FastAPI service for recipe CRUD, tags, mocked imports, and planner stubs per PRD.

## Requirements
- Python 3.11+
- Poetry

## Setup
```bash
poetry install
cp .env.example .env  # edit secrets as needed
poetry run alembic upgrade head
poetry run uvicorn jarvis_recipes.app.main:app --reload --port 7030
```
API docs: http://localhost:7030/docs

## Docker
```bash
docker-compose up --build
# API at http://localhost:7030/docs
```

## Tests
```bash
poetry run pytest
```

# jarvis-recipes-server
