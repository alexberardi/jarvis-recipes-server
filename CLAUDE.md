# jarvis-recipes-server

Recipe CRUD + URL/image recipe parsing + AI-powered meal planning. Owned by a single user persona ("the home cook"). Mostly accessed from `jarvis-recipes-mobile`, plus voice surfaces through command-center.

> **Two flavors of work here:** the synchronous CRUD path (recipes, tags, stock, meal-plan reads) and the asynchronous parsing path (URL → parsed recipe, image → OCR → parsed recipe, meal-plan generation via LLM). Async jobs go through Redis RQ.

## Topology

```
Mobile / CC
   │ user JWT (Authorization: Bearer …) for every domain route
   ▼
┌────────────────────────────────────────────┐
│  jarvis-recipes-server :7030               │
│  ├─ CRUD (recipes, tags, stock, planner)   │
│  ├─ /recipes/parse-url/async               │  ──▶  Redis RQ
│  ├─ /recipes/from-image/jobs               │
│  ├─ /meal-plans/generate/jobs              │
│  └─ /settings/*  (app creds OR superuser)  │
└──────┬─────────────────────────────────────┘
       │
       ├──▶ Postgres (recipe data + settings)
       ├──▶ Redis (async job queue, RQ)
       ├──▶ jarvis-ocr-service     (image → text, batch endpoint)
       ├──▶ jarvis-llm-proxy-api   (AI extraction, meal planning)
       ├──▶ MinIO / S3              (recipe images)
       └──▶ External recipe sites  (URL parsing, BeautifulSoup)

Queue worker (scripts/run_rq_worker.py) — separate process
   └─ pulls jobs off jarvis.recipes.jobs, does the work, updates DB
```

## Quick Reference

```bash
# Dev
poetry install
poetry run alembic upgrade head
./run.sh                                     # docker compose dev stack (API + worker)

# Or without Docker: API on :7030 plus the worker in a second terminal
poetry run uvicorn jarvis_recipes.app.main:app --port 7030 --reload
poetry run python scripts/run_rq_worker.py

# Tests
poetry run pytest -v --tb=short
poetry run pytest --cov --cov-report=term    # CI gate is --cov-fail-under=48
```

## Dependency graph

**Upstream (recipes depends on):**
- **PostgreSQL** (required) — recipes, ingredients, meal plans, parse jobs, settings
- **Redis** (required for async parsing + meal planning; sync CRUD works without it)
- **jarvis-auth** (required — every domain route validates a user JWT locally, and `/settings/*` round-trips to auth)
- **jarvis-config-service** (service discovery for auth / OCR / llm-proxy)
- **jarvis-ocr-service** (port 7031, image parsing only)
- **jarvis-llm-proxy-api** (port 7704, AI extraction + meal planning — `/v1/chat/completions`)
- **MinIO/S3** (optional) — image storage

**Downstream (consumers):**
- **jarvis-recipes-mobile** — primary consumer, full app
- **jarvis-command-center** — voice commands ("add this recipe", "what should I make tonight?")

**Impact if down:** no recipe access, no meal planning. Mobile app non-functional.

## Lifecycle / common operations

### 1. Create from URL (the most common parse path)

```
Mobile → POST /recipes/parse-url/async { url }
            │
            ├─ insert recipe_parse_jobs row (status=pending)
            ├─ enqueue RQ job on jarvis.recipes.jobs
            └─ return ParseJobStatus { id, status }

Mobile polls GET /recipes/jobs/{job_id}
            │
            └─ worker meanwhile:
                ├─ url_parsing/html_fetcher preflights + fetches (SSRF-guarded)
                ├─ extractors/schema_org  → JSON-LD Recipe (the fast path)
                ├─ extractors/heuristic   → DOM heuristics when there's no schema.org
                ├─ extractors/llm         → LLM extraction as the last resort
                ├─ url_parsing/ingredient_parser normalizes quantities + units
                └─ inserts Recipe + Ingredient + Step rows, marks job complete

Mobile polls succeeds → GET /recipes/{id}
```

There is also a synchronous `POST /recipes/parse-url` that runs the same chain inline. It exists for debugging and for callers that can wait; mobile uses the async route.

### 2. Create from image (OCR + LLM)

```
Mobile → POST /recipes/from-image/jobs (multipart)
            │
            ├─ size-gate each upload (image.max_bytes), downscale for vision
            ├─ upload to MinIO/S3, insert recipe_ingestions row
            ├─ enqueue RQ job
            └─ 202 { ingestion_id, job_id }

Worker (services/image_ingest_pipeline.py):
   ├─ services/ocr_service_client calls jarvis-ocr-service's BATCH endpoint;
   │  that service picks the provider (tesseract / easyocr / paddleocr / apple vision / LLM)
   ├─ services/ocr_quality scores the combined text and gates it
   ├─ llm_client.call_text_structuring turns text into a RecipeDraft (lightweight model)
   ├─ llm_client.clean_and_validate_draft tidies it
   └─ inserts Recipe etc.

Mobile polls GET /recipes/jobs/{job_id}
```

### 3. Meal planning

```
Mobile → POST /meal-plans/generate/jobs { preferences, … }   → 202 { job_id }
Mobile polls GET /meal-plans/generate/jobs/{job_id}
            │
            └─ worker: services/meal_plan_service → llm-proxy /v1/chat/completions
                ├─ ranks candidate recipes per slot
                └─ persists MealPlan + MealPlanItem rows
```

`/planner/draft` → `/planner/commit` is the interactive variant: draft a plan, let the user edit, then commit. `/planner/current` reads back the active plan.

### 4. Stock management

`GET /ingredients/stock` and `GET /units/stock` serve the curated static ingredient/unit lists that the mobile pickers use (seeded from `static_data/` via `POST /admin/static-data/seed`). These are read-only over HTTP; there is no user-writable pantry endpoint today.

## "How to..." recipes

### Improve URL parsing for a site

`services/url_parsing/` is a **three-tier chain**, not one module per site: `extractors/schema_org.py` (JSON-LD, handles most modern recipe sites), `extractors/heuristic.py` (DOM sniffing), `extractors/llm.py` (LLM fallback). Fix the tier that's failing rather than adding a site-specific parser. Tests live in `tests/test_url_recipe_parser.py` and `tests/test_html_fetcher_ssrf.py`.

### Add a new ingredient parser rule

`services/url_parsing/ingredient_parser.py` plus `services/quantity_parser.py` (regex + unit table). Tests: `tests/test_quantity_parser.py`, `tests/test_quantity_split.py`. Add the pattern and a test case before merging.

### Add a new meal-plan preference dimension

1. Add the field to the schema in `schemas/meal_plan.py` (Pydantic).
2. Inject it into the LLM prompt in `services/meal_plan_service.py`.
3. Update mobile to expose the preference.

### Add a new async job type

1. Define the handler in `services/queue_worker.py`.
2. Add the dispatch case in `process_job()` (same module).
3. Add a Pydantic request shape under `schemas/`.
4. Add a route that enqueues via `services/queue_service.enqueue_job` and returns a job id.
5. Same RQ queue (`jarvis.recipes.jobs`) — no new queues needed.

### Add a runtime setting

`services/settings_service.py` → add a `SettingDefinition`, seed it in a **new** alembic migration, and **read it somewhere**. `tests/test_settings.py::test_every_definition_has_a_consumer` fails if you skip the last step; `test_definitions_match_the_seed_migration` fails if the seed row and the declared default drift apart.

## Invariants & gotchas

1. **The queue worker is a separate process.** `scripts/run_rq_worker.py`. Without it, async endpoints accept jobs that never complete. Docker compose starts both. (`scripts/run_parse_worker.py` is the older DB-polling worker; nothing in compose runs it.)
2. **OCR provider choice belongs to jarvis-ocr-service.** This service calls the batch endpoint with `provider="auto"` and gates the result with `services/ocr_quality.py` (char/line counts + a gibberish heuristic). **Don't add strict consensus rules here** — recipe images are noisy and best-effort beats high-confidence-no-result.
3. **The extractor chain is the fallback story.** schema.org → heuristic → LLM. Sites change their DOM constantly; the LLM tier is why that doesn't page anyone. Don't break the chain.
4. **Quantity parsing is permissive.** "1.5 cups" / "1½ cups" / "1 1/2 cup" all need to parse to `1.5` cups — unicode fractions, mixed forms, etc. **When adding a new format, write the test first.**
5. **JWT verification accepts HS256 *and* RS256, and binds the key to the algorithm FAMILY.** jarvis-auth is migrating HS256 → RS256; a verifier pinned to one algorithm makes a staged rollout impossible, since tokens minted before the flip must keep working after it. `api/deps.py` takes the algorithm from the token header, checks it against an allowlist (so `"none"` is rejected), then picks key material by family: HS256 → `AUTH_SECRET_KEY`, RS256 → jarvis-auth's published public key.

   **Do not "simplify" that into one shared key variable.** The RSA public key is published at `jarvis-auth /auth/public-key` for anyone to read. If a single variable held "the key", an attacker could sign a token HS256 using that public key as the HMAC secret and be verified. `tests/test_settings.py::test_hs256_signed_with_the_public_key_is_rejected` forges exactly that by hand and must keep passing.

   The RS256 public key is fetched over plain HTTP and cached for the process lifetime, so a *running* service keeps verifying if jarvis-auth goes down; only a cold start during an outage fails, and it fails closed. Implemented with `httpx` rather than `jarvis-auth-client` on purpose — this service is being decoupled from the stack, and a new Jarvis library dependency just to verify a token moves it the wrong way. `auth.algorithm` was dropped (migration `d4e5f6a7b8c9`): it only ever governed what jarvis-auth *mints*, which is not this service's business.
6. **Two database URLs (`DATABASE_URL` + `MIGRATIONS_DATABASE_URL`).** `apply_migrations.sh` and `scripts/make_migration.py` prefer the migration URL; it is often the host-network form while `DATABASE_URL` is the container one.
7. **Every domain route requires a user JWT.** `deps.verify_app_auth` exists and is imported by `main.py`, but it is **not attached to any router** — app-to-app credentials do not open the recipes API. The only app-creds surface is `/settings/*` (reads via combined auth; writes are superuser-JWT only).
8. **No multi-household scoping.** `household_id` exists only on the `settings` table. Recipes/meal plans are keyed by `user_id`. If you add household scoping, audit every query — there is no filter pattern in place.
9. **`GET /recipes/core/{id}` is a stub.** It always 404s; there is no core recipe store.
10. **`GET /recipes/jobs` is unreachable.** `GET /recipes/{recipe_id}` (int) is registered first, so `"jobs"` fails path validation and the caller gets a 422. Use the `/recipes/parse-url/jobs` alias. Fixing it means moving the literal-path routes above the `/{recipe_id}` routes in `routes/recipes.py`; `tests/test_recipes.py::test_bare_recipes_jobs_path_is_shadowed` pins the current behaviour so the fix can't land silently.

## API surface

Regenerate this section from the app's own schema rather than by hand:

```bash
poetry run python -c "
from jarvis_recipes.app.main import app
import json; print(json.dumps(app.openapi()['paths'], indent=2))"
```

Unless noted, every route below takes a **user JWT** (`Authorization: Bearer <jwt>`).

### Recipes
| Method | Path | Notes |
|---|---|---|
| GET | `/recipes` | list for the current user |
| POST | `/recipes` | manual create (201) |
| GET | `/recipes/{recipe_id}` | detail with ingredients + steps |
| PATCH | `/recipes/{recipe_id}` | partial update (**not** PUT) |
| DELETE | `/recipes/{recipe_id}` | 204 |
| GET | `/recipes/user/{recipe_id}` | explicit user-scoped read |
| GET | `/recipes/stage/{stage_id}` | staged (not yet committed) recipe |
| GET | `/recipes/stock` | curated stock recipes from `static_data/` |
| GET | `/recipes/core/{recipe_id}` | stub — always 404, no auth |

### Parsing (async unless noted)
| Method | Path | Notes |
|---|---|---|
| POST | `/recipes/parse-url` | **synchronous** parse, returns the recipe |
| POST | `/recipes/parse-url/async` | enqueue, returns ParseJobStatus |
| POST | `/recipes/parse-payload/async` | enqueue from a pre-fetched payload (webview capture) |
| POST | `/recipes/from-image/jobs` | multipart upload, 202 |
| POST | `/recipes/import/url` | one-shot import, returns a RecipeDraft |
| POST | `/recipes/import/image` | one-shot import, returns a RecipeDraft |
| GET | `/recipes/parse-url/jobs` | list jobs — **use this one**, the `/recipes/jobs` alias is shadowed (gotcha 10) |
| GET | `/recipes/jobs/{job_id}` | poll status (alias: `/recipes/parse-url/status/{job_id}`) |
| POST | `/recipes/jobs/{job_id}/cancel` | (alias: `/recipes/parse-url/jobs/{job_id}/cancel`) |

### Meal planning
| Method | Path | Notes |
|---|---|---|
| POST | `/meal-plans/generate/jobs` | enqueue LLM generation (202) |
| GET | `/meal-plans/generate/jobs/{job_id}` | poll status / result |
| POST | `/planner/draft` | interactive draft |
| POST | `/planner/commit` | persist a drafted plan |
| GET | `/planner/current` | active plan |

### Stock, tags, admin
| Method | Path | Notes |
|---|---|---|
| GET | `/ingredients/stock` | curated ingredient list (read-only) |
| GET | `/units/stock` | curated unit list (read-only) |
| GET | `/tags` | list tags |
| POST | `/tags` | create tag (201) |
| POST | `/recipes/{recipe_id}/tags/{tag_id}` | attach |
| DELETE | `/recipes/{recipe_id}/tags/{tag_id}` | detach (204) |
| POST | `/admin/static-data/seed` | `X-Admin-Secret` header, not JWT |
| POST | `/admin/static-recipes/seed` | `X-Admin-Secret` header, not JWT |

### Settings + health
| Method | Path | Auth |
|---|---|---|
| GET | `/settings/` , `/settings/categories` , `/settings/{key}` | app creds **or** superuser JWT |
| PUT | `/settings/{key}` | superuser JWT only |
| POST | `/settings/sync-from-env` , `/settings/invalidate-cache` | superuser JWT only |
| GET | `/health` | none — `{"status": "ok"}` |

## Data model

Tables (see `jarvis_recipes/app/db/models.py`):
- `users` — mirror of the auth user id
- `recipes` — user-scoped: title, source_type/source_url, image_url, servings, total_time
- `ingredients` — **child rows of a recipe** (recipe_id FK), not a shared normalized table
- `steps` — ordered instruction rows (unique on recipe_id + step_number)
- `tags` + `recipe_tags` — many-to-many
- `meal_plans` / `meal_plan_items` — user-scoped plan + per-date/meal-type recipe assignment
- `recipe_parse_jobs` — async job tracking (url / image / ingestion / meal_plan_generate)
- `recipe_ingestions` — image-ingestion attempts + pipeline telemetry
- `stage_recipes` — parsed-but-uncommitted recipes
- `mailbox_messages` — worker → API notifications
- `stock_ingredients` / `stock_units_of_measure` — curated static reference data
- `settings` — multi-tenant runtime settings (the only table with `household_id`)

Migrations: 10 under `alembic/versions/`. Current head is `d4e5f6a7b8c9`. **Use `alembic heads`** — grepping `down_revision` lies, and at least one migration's docstring disagrees with its actual `down_revision`.

## Config surface

Secrets, discovery and bootstrap values only. **Runtime knobs live in the settings DB**, not here.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` / `MIGRATIONS_DATABASE_URL` | yes | Postgres |
| `AUTH_SECRET_KEY` | yes | HS256 JWT validation (must match jarvis-auth). Still required during the RS256 migration window; once jarvis-auth mints RS256 only, this service needs no shared secret at all. |
| `ADMIN_SECRET` | yes | `X-Admin-Secret` for the `/admin/*` seed routes |
| `JARVIS_ENV` | no (`development`) | `production` makes weak secrets fatal at boot |
| `JARVIS_CONFIG_URL` | yes | Service discovery |
| `JARVIS_APP_ID` / `JARVIS_APP_KEY` | yes | Outbound app-to-app creds (llm-proxy, OCR, jarvis-logs) |
| `JARVIS_AUTH_BASE_URL` | **required for RS256** | Auth URL, used if discovery misses. `deps._rs256_public_key()` fetches `{auth_url}/auth/public-key` from here; if neither this nor `JARVIS_CONFIG_URL` resolves, every RS256 token 401s. |
| `JARVIS_OCR_SERVICE_URL` | optional | Gates the OCR tier in the image pipeline |
| `LLM_BASE_URL` | optional | Legacy llm-proxy URL, used if discovery misses |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | required (queue) | RQ |
| `S3_ENDPOINT_URL` / `S3_REGION` / `S3_BUCKET` / `AWS_*` | optional | MinIO/S3 for images |
| `SCRAPER_COOKIES` | optional | JSON cookie jar for gated recipe sites |
| `JARVIS_LOG_CONSOLE_LEVEL` / `JARVIS_LOG_REMOTE_LEVEL` | no (INFO) | See Logging below |

### Runtime settings (settings DB, `/settings/*`, jarvis-admin)

Declared in `services/settings_service.py`, seeded by `alembic/versions/c3d4e5f6a7b8_*`. Each falls back to the env var listed when there is no DB row.

| Key | Default | env_fallback |
|---|---|---|
| `llm.full_model_name` | `live` | `JARVIS_FULL_MODEL_NAME` |
| `llm.lightweight_model_name` | `live` | `JARVIS_LIGHTWEIGHT_MODEL_NAME` |
| `queue.max_retries` | `3` | `LLM_RECIPE_QUEUE_MAX_RETRIES` |
| `parse_job.abandon_minutes` | `4320` | `RECIPE_PARSE_JOB_ABANDON_MINUTES` |
| `image.max_bytes` | `10485760` | `RECIPE_IMAGE_MAX_BYTES` |
| `scraper.user_agent` | Chrome 124 UA | `SCRAPER_USER_AGENT` |

## Logging

`core/logging_config.py` owns both sinks and is called by `main.py` and by every script in `scripts/`.

- `setup_console_logging()` runs at import time in `main.py`, **before** `create_app()` — the Dockerfile CMD is a bare uvicorn, which configures only the `uvicorn*` loggers, so without this nothing this package logs reaches a handler.
- `setup_remote_logging()` runs in the startup event and attaches a `JarvisLogHandler` (jarvis-log-client → jarvis-logs) to the root logger and to `uvicorn` / `uvicorn.error` / `uvicorn.access`. It no-ops when `JARVIS_APP_KEY` is unset.
- Short-lived processes must call `shutdown_remote_logging()` in a `finally` — it closes the handler, which flushes the batch. The scripts already do.
- **Gotcha:** RQ forks a work-horse per job and the handler's flush thread does not survive the fork, so logs emitted *inside* a job do not reach Loki today. Log job outcomes from the parent, or switch to `SimpleWorker`.

## Architecture

```
jarvis_recipes/app/
├── main.py                          # FastAPI factory, /health, settings router mount
├── api/
│   ├── deps.py                      # get_current_user (JWT), verify_app_auth (unwired)
│   └── routes/
│       ├── recipes.py   meal_plans.py  planner.py  stock.py  tags.py
│       └── from_image.py  ingestion.py  import.py
├── core/
│   ├── config.py                    # pydantic: secrets + bootstrap only
│   ├── logging_config.py            # console + jarvis-logs wiring
│   └── service_config.py            # jarvis-config-client wrapper + env fallbacks
├── db/                              # base.py / models.py / session.py
├── schemas/                         # Pydantic request/response shapes
└── services/
    ├── recipes_service.py  ingestion_service.py  planner_service.py
    ├── meal_plan_service.py  parse_job_service.py  stock_service.py
    ├── llm_client.py                # llm-proxy calls + JSON repair
    ├── ocr_service_client.py        # jarvis-ocr-service batch client
    ├── ocr_quality.py               # gibberish / quality gate on OCR text
    ├── image_ingest_pipeline.py     # image → OCR → draft
    ├── image_ingest_worker.py       # RQ entry point for image jobs
    ├── queue_service.py             # RQ wrapper (jarvis.recipes.jobs)
    ├── queue_worker.py              # job dispatch + handlers
    ├── settings_service.py          # SettingDefinitions + singleton
    ├── quantity_parser.py           # unit + fraction parsing
    ├── s3_storage.py  storage/      # object storage
    └── url_parsing/
        ├── html_fetcher.py          # SSRF preflight + fetch
        ├── ingredient_parser.py  parsing_utils.py  constants.py  models.py
        └── extractors/              # schema_org.py → heuristic.py → llm.py

scripts/
├── run_rq_worker.py                 # the worker compose runs
├── run_parse_worker.py              # legacy DB-polling worker
├── run_cleanup.py                   # abandon stale jobs, prune stage recipes
└── make_migration.py
alembic/versions/                    # 9 migrations
tests/                               # 18 modules
static_data/                         # curated ingredients / units / stock recipes
```

## Testing

```bash
poetry run pytest -v --tb=short
```

Integration tests are marked `integration` and deselected by default (`pytest.ini` addopts `-m "not integration"`). Coverage has **no omit list** — the CI gate is the honest floor, currently 48%. The big untested surfaces are `queue_worker.py`, `llm_client.py` and `image_ingest_worker.py`.

Covers: URL parsing + SSRF preflight, image pipeline (mocked OCR), meal plan generation (mocked LLM), quantity parsing, stock endpoints, recipe CRUD, secret guard, settings definitions/service/routes, JWT verification (HS256 + RS256 dual-accept, the public-key fetch and its failure modes), `/health`.

`tests/test_settings.py` is also where the JWT verification tests live (historical, and the file is large as a result): `TestVerificationAcceptsBothAlgorithms` covers the HS256/RS256 window and the algorithm-confusion forgery, `TestRs256PublicKeyFetch` covers the key fetch, its caching and every way it can fail. It carries two cheap static guards worth keeping too: every `SettingDefinition` must be read somewhere, and every module that calls `get_settings_service()` must import it (a missing import is a NameError only the live route would surface).

## Failure modes

| Failure | Behavior |
|---|---|
| Postgres down | All endpoints 5xx |
| Redis down | Sync CRUD works; async parse / meal-plan endpoints fail with a clear error |
| OCR service down | Image jobs fail; URL parse and CRUD unaffected |
| LLM proxy down | URL parse falls back to schema.org + heuristic tiers; meal planning fails |
| Worker not running | Jobs queue forever; mobile shows pending until `parse_job.abandon_minutes` elapses |
| Neither `JARVIS_CONFIG_URL` nor the legacy URL env var set | `/settings/*` 500s with "Cannot discover jarvis-auth"; OCR/LLM calls raise at call time |
| Recipe site changes DOM | schema.org tier usually still works; otherwise heuristic, then LLM |

## Out of scope / explicitly not here

- **Real-time grocery integration** (Instacart, etc.). Meal plans generate lists; delivery is the user's problem.
- **Nutritional analysis.** No calorie / macro calculation today.
- **Recipe sharing between households.** Recipes are user-scoped.
- **Cooking-step timers / voice integration.** Voice is handled by command-center; this service just stores instructions.
