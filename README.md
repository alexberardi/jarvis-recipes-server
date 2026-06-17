# Jarvis Recipes API

FastAPI service for recipe management and AI-powered meal planning, part of the
Jarvis self-hosted assistant stack.

It ships:
- **Recipe CRUD** — recipes, ingredients, tags, and pantry stock.
- **Import from URL** — fetches a page and extracts a structured recipe via
  schema.org parsing, heuristics, and an LLM fallback (`jarvis-llm-proxy-api`).
  Outbound fetching is SSRF-hardened (private/loopback/link-local hosts are
  blocked, and every redirect hop is re-validated).
- **Import from image** — OCR through `jarvis-ocr-service` (multi-provider
  ensemble), then the same LLM extraction path.
- **AI meal planning** — generates meal plans and shopping lists via the LLM
  proxy, advised by current pantry stock.
- **Async job queue** — URL/image parsing and meal-plan generation run on a
  Redis RQ worker (separate process); endpoints return a `job_id` to poll.

See [CLAUDE.md](CLAUDE.md) for the full architecture, topology, and invariants.

## Requirements
- Python 3.11+
- Poetry
- PostgreSQL and Redis (Redis required only for the async parsing / meal-plan path)

## Setup
```bash
poetry install
cp .env.example .env  # edit secrets as needed
poetry run alembic upgrade head
poetry run uvicorn jarvis_recipes.app.main:app --reload --port 7030
# In a second terminal, start the queue worker (async parsing + meal planning):
poetry run python scripts/run_rq_worker.py
```
API docs: http://localhost:7030/docs

## Docker
The dev compose file runs both the API and the queue worker:
```bash
docker compose -f docker-compose.dev.yaml up --build
# API at http://localhost:7030/docs
```
Add `--profile standalone` to also bring up local Postgres and Redis containers.

## Tests
```bash
poetry run pytest
```

## URL scraping — operator responsibility

The URL import path fetches and parses third-party recipe pages. **It is
intended only for sources you (the operator) are permitted to fetch.** You are
responsible for complying with each source site's Terms of Service and
`robots.txt`.

- `SCRAPER_COOKIES` is **user-supplied**. If you provide cookies, you are
  asserting you have permission to access that content with those credentials.
- The `r.jina.ai` reader proxy is used only as a **fallback** when a direct
  fetch is blocked; it is a user-configured external service, not something this
  project operates. SSRF guards still apply to the original URL before any proxy
  fetch.

This project does not bundle credentials for or endorse scraping any particular
site.
