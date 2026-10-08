# Jarvis Recipes API

FastAPI service for recipe management and AI-powered meal planning, part of the
Jarvis self-hosted assistant stack.

It ships:
- **Recipe CRUD** — recipes, ingredients, tags, and pantry stock.
- **Import from URL** — fetches a page and extracts a structured recipe via
  schema.org parsing, heuristics, and an LLM fallback (`jarvis-llm-proxy-api`).
  Outbound fetching is SSRF-hardened (private/loopback/link-local hosts are
  blocked, and every redirect hop is re-validated).
- **Import from image** — OCR through jarvisd's async OCR job API (the images go
  over HTTP, the result comes back on a callback), then the same LLM extraction
  path. The legacy `jarvis-ocr-service` Redis queue is still selectable.
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

## Running against jarvisd

jarvisd (the single-binary Jarvis server) serves auth, discovery, OCR and the
LLM itself; recipes stays a separate **add-on** that talks to it over HTTP. It
needs its own Postgres and Redis (`--profile standalone` brings both up) and an
S3-compatible store for photos.

### 1. Register the add-on in jarvisd (admin → Connections)

Two things, both on the Connections page (AD7) as a superuser:

1. **External service** — name `jarvis-recipes-server`, URL the base URL
   *jarvisd* can reach recipes at (e.g. `http://10.0.0.5:7030`), health path
   `/health`. Discovery hands this URL to the mobile app and command-center, and
   recipes reads it back to build the OCR callback URL, so it must be an address
   jarvisd itself can route to (not `localhost` unless they share a host
   network).
2. **App client** — app id `jarvis-recipes-server`. The key is shown **once**;
   it goes into recipes' `.env` as `JARVIS_APP_KEY`.

The same over the admin API, if you prefer curl (`$T` is a superuser access
token from `POST {auth}/auth/login`; the admin listener is 7710 by default):

```bash
curl -X POST http://jarvisd:7710/api/connections/services -H "Authorization: Bearer $T" \
  -H 'Content-Type: application/json' \
  -d '{"name":"jarvis-recipes-server","url":"http://10.0.0.5:7030","health_path":"/health","description":"Recipes add-on"}'
curl -X POST http://jarvisd:7710/api/connections/apps -H "Authorization: Bearer $T" \
  -H 'Content-Type: application/json' -d '{"app_id":"jarvis-recipes-server","name":"Recipes add-on"}'
```

### 2. Configure recipes

| Variable | Value |
|---|---|
| `JARVIS_CONFIG_URL` | jarvisd's config listener, e.g. `http://jarvisd:7700` |
| `JARVIS_APP_ID` / `JARVIS_APP_KEY` | the app client from step 1 |
| `AUTH_SECRET_KEY` | **leave unset.** jarvisd mints RS256 only; recipes fetches the public key from `/auth/public-key`. An unset key turns HS256 off entirely. |
| `RECIPES_PUBLIC_URL` | optional: overrides the registry URL for OCR callbacks |
| `DATABASE_URL`, `REDIS_*`, `S3_*`, `ADMIN_SECRET` | as before |

The `ocr.transport` setting (settings DB, default `http`) selects jarvisd's OCR
job API. Set it to `redis` only for a legacy stack still running the Python
`jarvis-ocr-service` workers.

### What the app credentials are for

| Direction | Call | Auth |
|---|---|---|
| recipes → jarvisd | `POST /v1/ocr/jobs`, `/v1/chat/completions`, `/v1/ocr/batch`, log shipping | recipes' app client |
| jarvisd → recipes | `POST /internal/ocr/callback` (OCR job finished) | **jarvisd's own** app client (`jarvisd`, minted by jarvisd on first use), checked by recipes against `{auth}/internal/app-ping` |
| anyone → recipes `/settings/*` reads | | app credentials or a superuser JWT |

User routes take the user's jarvisd access token (RS256, `kid` header); a token
signed by a key recipes has not seen makes it refetch the public key, at most
once every 30 s.

### Moving data from the legacy stack

jarvisd starts with fresh accounts, so users and households get new ids. After
everyone has signed up again, re-own the recipes data by email:

```bash
python -m scripts.remap_users \
  --legacy-auth-db postgresql://postgres:...@localhost:5432/jarvis_auth \
  --jarvisd-url http://jarvisd:7701 --jarvisd-email admin@example.com   # report only
# ...then the same with --apply
```

It matches users by email (case-insensitive), maps each legacy household to its
owner's new household, rewrites every user/household column in one transaction
and logs what it did in `legacy_id_remap`, so a re-run changes nothing and a
later run picks up people who signed up since. Unmatched rows are left alone
and listed; collisions that would hand rows to the wrong account are refused.
See the script's docstring for the rules.

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
