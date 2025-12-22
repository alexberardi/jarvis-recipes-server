# Queue Flow Design (Recipes ↔ OCR)

## Goal
Enable recipe import/creation requests (especially image-based) to run asynchronously using Redis-backed queues, with clean handoffs between **jarvis-recipes-server** and **jarvis-ocr-service**.

Primary goals:
- Fast user response (request accepted immediately; work happens in background)
- Clear workflow state and debuggability
- Minimal coupling between services (OCR should not need to know “recipes” internals beyond where to send a completion event)
- Small queue messages (refs, not large blobs)

## Summary of the Approach
We will implement a **baton-pass workflow** using queues:
- The **API** routes jobs to the correct initial queue based on request type (image vs non-image).
- **OCR jobs** are queued directly to OCR (no extra hop).
- OCR emits a completion event back to the recipes queue via a `reply_to` target.
- Recipes continues processing and finalizes the recipe.

This avoids wasting a worker cycle just to re-route work we already know upfront.

## Services
- **jarvis-recipes-server**
  - Receives mobile/web requests
  - Creates workflow/job records
  - Enqueues initial jobs
  - Runs workers that build recipe entities from inputs and tool outputs

- **jarvis-ocr-service**
  - Consumes OCR jobs
  - Performs OCR extraction
  - Produces OCR output (text + metadata) in the completion queue message (no persistence in v1)
  - Emits completion events (success/failure)

## Queues
### Naming
Use namespaced, service-owned queues (ownership = consumption).

- `jarvis.recipes.jobs` (consumed by recipes workers)
- `jarvis.ocr.jobs` (consumed by OCR workers)

Producers may enqueue into other service queues (trusted internal network), but the message contract is centrally defined and validated.

### Transport
V1 can use Redis Lists (simple). V2 can migrate to Redis Streams for better retries/ack visibility.

## Workers & “Triggers” (How services listen)
Redis queues are not HTTP/event hooks; workers typically use **blocking reads** (or a queue library like RQ) so they “wake up” immediately when a job arrives.

### V1 worker pattern (RQ)
Each service runs one or more worker processes that block on their queue:
- Recipes worker(s) listen on `jarvis.recipes.jobs`
- OCR worker(s) listen on `jarvis.ocr.jobs`

RQ uses Redis under the hood and workers effectively behave like event-driven consumers.

#### Compose/networking
Because Redis is hosted in a centralized `jarvis-data-stores` compose (alongside Postgres), each app compose must be able to reach that Redis instance.

Recommended approach (v1): **Host port mapping**

Expose Redis on a host port from the centralized `jarvis-data-stores` compose, and have each app connect via `host.docker.internal`.

### Centralized compose: expose Redis
Example (`jarvis-data-stores/docker-compose.yml`):

```yaml
services:
  redis:
    image: redis:7
    ports:
      - "6379:6379"
```

### App composes: connect via host.docker.internal
Example (`jarvis-recipes-server/docker-compose.yml` and `jarvis-ocr-service/docker-compose.yml`):

```yaml
services:
  app:
    environment:
      REDIS_HOST: host.docker.internal
      REDIS_PORT: 6379
```

Linux `extra_hosts` fallback example:

```yaml
services:
  app:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### Queue consumption rules
- Only the owning service consumes from its queue.
- Producers may enqueue into other service queues (trusted internal environment).
- Messages must be JSON and validated by the consumer.

### Operational notes
- Run at least 1 worker per service in dev; scale workers independently by job type.
- Add a small `max_new_tokens` cap and stop sequences for any LLM-in-worker steps to prevent runaway outputs.
- Prefer running Redis on the same machine as workers for low-latency queue ops; for home use, host-port mapping is sufficient.

## Fast-Path Routing (Initial POST)
Because the initial API request *already knows* the import type:

- If request includes an image/photo upload → enqueue **directly** to `jarvis.ocr.jobs`
- If request is URL/manual text → enqueue to `jarvis.recipes.jobs`

This reduces latency and eliminates a “router worker” step.

## End-to-End Flows

### Flow A — Image Import
1. **Mobile App → Recipes API**
   - `POST /imports` (or `/recipes/import`) with image reference (uploaded file id / object key)
   - Recipes creates `workflow_id` + initial record(s) and returns immediately

2. **Recipes API → OCR Queue (direct)**
   - Enqueue `ocr.extract_text.requested` to `jarvis.ocr.jobs`
   - Include `workflow_id`, `job_id`, and `reply_to = jarvis.recipes.jobs`

3. **OCR Worker**
   - Pops `ocr.extract_text.requested`
   - Performs OCR
   - Computes validity + confidence per image and prepares completion payload (includes `results[]` aligned by index, with `ocr_text`, `is_valid`, `text_len`, `confidence`, and `tier`).

4. **OCR → Recipes Queue (callback)**
   - Enqueue `ocr.completed` (or `recipe.import.ocr_completed`) to `jarvis.recipes.jobs`
   - Include `workflow_id`, `parent_job_id`, `results[] (and meta)`, and status

5. **Recipes Worker (continue)**
   - Pops callback event
   - Reads OCR text directly from the completion message payload (`payload.results[]`), preserving image order by index.
   - Runs parsing/LLM extraction/normalization
   - Creates/updates recipe records
   - Marks workflow complete

### Flow B — URL Import
1. Mobile → Recipes API (`POST /imports` with URL)
2. Recipes enqueues `recipe.import.url.requested` to `jarvis.recipes.jobs`
3. Recipes worker fetches HTML, extracts text, runs LLM parsing
4. Recipes completes

### Flow C — Manual Entry
1. Mobile → Recipes API (manual fields)
2. Recipes enqueues `recipe.create.manual.requested` to `jarvis.recipes.jobs`
3. Recipes worker validates + persists

## Message Contracts
Redis queues do not have “columns.” We define a shared **envelope** (contract) and validate it in consumers.

### Common Envelope (v1)
All messages are JSON.

```json
{
  "schema_version": 1,
  "job_id": "uuid",
  "workflow_id": "uuid",
  "job_type": "string",
  "source": "string",
  "target": "string",
  "created_at": "ISO-8601",
  "attempt": 1,
  "reply_to": "optional queue name",
  "payload": {},
  "trace": {
    "request_id": "optional",
    "parent_job_id": "optional"
  }
}
```

### OCR Request
`job_type = "ocr.extract_text.requested"`

```json
{
  "schema_version": 1,
  "job_id": "...",
  "workflow_id": "...",
  "job_type": "ocr.extract_text.requested",
  "source": "jarvis-recipes-server",
  "target": "jarvis-ocr-service",
  "created_at": "...",
  "attempt": 1,
  "reply_to": "jarvis.recipes.jobs",
  "payload": {
    "image_refs": [
        { "kind": "local_path|s3|minio|db", "value": "s3://my-bucket/recipe-images/<user_id>/<ingestion_id>/0.jpg", "index": 0 },
        { "kind": "local_path|s3|minio|db", "value": "s3://my-bucket/recipe-images/<user_id>/<ingestion_id>/1.jpg", "index": 1 }
],
    "options": {
      "language": "en"
    }
  },
  "trace": {
    "request_id": "...",
    "parent_job_id": null
  }
}
```

### OCR Completion Event
`job_type = "ocr.completed"`

```json
  "payload": {
    "status": "success|failed",
    "results": [
      {
        "index": 0,
        "ocr_text": "<full extracted text for image 0, may be truncated>",
        "truncated": false,
        "meta": {
          "language": "en",
          "confidence": 0.0,
          "text_len": 0,
          "is_valid": true,
          "tier": "tesseract|easyocr|paddleocr|apple_vision|llm_local|llm_cloud"
        },
        "error": null
      }
    ],
    "artifact_ref": null,
    "error": {
      "message": "optional",
      "code": "optional"
    }
  },
```

## Locked JSON Schemas (v1)
These schemas match the canonical contracts defined in `jarvis-ocr-service` and are copied here to keep Recipes and OCR aligned.

### Schema: OCR Request (`ocr.extract_text.requested`)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "jarvis.ocr.request.v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "job_id",
    "workflow_id",
    "job_type",
    "source",
    "target",
    "created_at",
    "attempt",
    "reply_to",
    "payload",
    "trace"
  ],
  "properties": {
    "schema_version": { "const": 1 },
    "job_id": { "type": "string" },
    "workflow_id": { "type": "string" },
    "job_type": { "const": "ocr.extract_text.requested" },
    "source": { "type": "string", "minLength": 1 },
    "target": { "type": "string", "minLength": 1 },
    "created_at": { "type": "string", "format": "date-time" },
    "attempt": { "type": "integer", "minimum": 1 },
    "reply_to": { "type": "string", "minLength": 1 },
    "payload": {
      "type": "object",
      "additionalProperties": false,
      "required": ["image_refs"],
      "properties": {
        "image_refs": {
          "type": "array",
          "minItems": 1,
          "maxItems": 8,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["kind", "value", "index"],
            "properties": {
              "kind": {
                "type": "string",
                "enum": ["local_path", "s3", "minio", "db"]
              },
              "value": { "type": "string", "minLength": 1 },
              "index": { "type": "integer", "minimum": 0 }
            }
          }
        },
        "options": {
          "type": "object",
          "additionalProperties": false,
          "properties": {
            "language": { "type": "string", "minLength": 1 }
          }
        }
      }
    },
    "trace": {
      "type": "object",
      "additionalProperties": false,
      "required": ["request_id", "parent_job_id"],
      "properties": {
        "request_id": { "type": ["string", "null"] },
        "parent_job_id": { "type": ["string", "null"] }
      }
    }
  }
}
```

### Schema: OCR Completion (`ocr.completed`)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "jarvis.ocr.completed.v1",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "job_id",
    "workflow_id",
    "job_type",
    "source",
    "target",
    "created_at",
    "attempt",
    "reply_to",
    "payload",
    "trace"
  ],
  "properties": {
    "schema_version": { "const": 1 },
    "job_id": { "type": "string" },
    "workflow_id": { "type": "string" },
    "job_type": { "const": "ocr.completed" },
    "source": { "const": "jarvis-ocr-service" },
    "target": { "type": "string", "minLength": 1 },
    "created_at": { "type": "string", "format": "date-time" },
    "attempt": { "type": "integer", "minimum": 1 },
    "reply_to": { "type": ["string", "null"] },
    "payload": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "results", "artifact_ref", "error"],
      "properties": {
        "status": { "type": "string", "enum": ["success", "failed"] },
        "results": {
          "type": "array",
          "minItems": 1,
          "maxItems": 8,
          "items": {
            "type": "object",
            "additionalProperties": false,
            "required": ["index", "ocr_text", "truncated", "meta", "error"],
            "properties": {
              "index": { "type": "integer", "minimum": 0 },
              "ocr_text": { "type": "string" },
              "truncated": { "type": "boolean" },
              "meta": {
                "type": "object",
                "additionalProperties": false,
                "required": ["language", "confidence", "text_len", "is_valid", "tier"],
                "properties": {
                  "language": { "type": "string", "minLength": 1 },
                  "confidence": { "type": "number", "minimum": 0.0, "maximum": 1.0 },
                  "text_len": { "type": "integer", "minimum": 0 },
                  "is_valid": { "type": "boolean" },
                  "tier": {
                    "type": "string",
                    "enum": ["tesseract", "easyocr", "paddleocr", "apple_vision", "llm_local", "llm_cloud"]
                  }
                }
              },
              "error": {
                "type": ["object", "null"],
                "additionalProperties": false,
                "required": ["message", "code"],
                "properties": {
                  "message": { "type": ["string", "null"] },
                  "code": { "type": ["string", "null"] }
                }
              }
            }
          }
        },
        "artifact_ref": { "type": ["object", "null"] },
        "error": {
          "type": ["object", "null"],
          "additionalProperties": false,
          "required": ["message", "code"],
          "properties": {
            "message": { "type": ["string", "null"] },
            "code": { "type": ["string", "null"] }
          }
        }
      }
    },
    "trace": {
      "type": "object",
      "additionalProperties": false,
      "required": ["request_id", "parent_job_id"],
      "properties": {
        "request_id": { "type": ["string", "null"] },
        "parent_job_id": { "type": ["string", "null"] }
      }
    }
  }
}
```

## Artifact Storage
Queue messages should contain **small payloads**. For v1 we include OCR text directly (<= 50 KB) and avoid persistence.

### V1 (no persistence)
For v1, we can avoid persisting OCR artifacts by including the extracted text directly in the **OCR completion** queue message, and computing `is_valid`/`confidence` inside the OCR service.

Constraints / guardrails:
- Set a hard max size for `payload.ocr_text` (example: 20–50 KB). If exceeded, OCR should either:
  - truncate and set `payload.truncated=true`, or
  - switch to the “artifact ref” path (see Future).
- Always include lightweight metadata (language, confidence, page count if available).
- Do **not** include raw images or large binary blobs in the queue message.

This keeps the system simple while we’re early and traffic is low.

### Future (if OCR output gets large)
If we start seeing frequent large outputs (multi-page PDFs, long receipts, etc.), move to storing OCR text as an artifact (Postgres/MinIO) and pass only an `artifact_ref` in the completion event.

## Workflow State & Status Tracking
Even with queue-based processing, we need durable status:
- `workflow_id` status: `RECEIVED → OCR_PENDING → OCR_DONE → PARSING → COMPLETE` (and `FAILED`)
- `job_id` status for each step

Recommended:
- A small `workflows` and `jobs` table in Recipes DB (or shared DB)
- Workers update status transitions

## Retries, Idempotency, and Safety
- **Idempotency**: Clients should provide (or server should generate) an `idempotency_key` per import request.
- **Deduplication**: Before enqueue, recipes can set a short-lived Redis key (`SETNX`) keyed by `idempotency_key`.
- **Retries**:
  - Each job has `attempt` incremented on retry.
  - Max attempts per job_type (e.g., OCR 3, parsing 2).
  - On final failure, enqueue a `*.failed` event back to recipes.
- **Loop prevention**:
  - Recipes must only continue a workflow if state progression is valid.
  - Add `max_steps` or a workflow guard counter if needed.

## Security / Trust Boundary
Assumption (v1): all services are on the same private Docker network.

If/when we separate networks or introduce untrusted callers:
- Introduce a Queue Gateway service that authenticates and validates job requests
- Producers submit `POST /jobs`, gateway routes to queues

## Resolved Decisions (v1)
1. Max size for each results[i].ocr_text in ocr.completed: **50 KB** (truncate and set results[i].truncated=true if exceeded).
2. Recipes reads OCR text directly from the completion message (payload.results[]). No artifact store in v1.
3. Orchestration model: Recipes acts as the workflow owner/runner for recipe import; OCR is a tool/service that emits `ocr.completed`.
4. User-facing progress model: Recipes exposes a polling endpoint for job/workflow status (existing `RecipeParseJob` pattern).
5. Transport: Start with Redis Lists/RQ; revisit Redis Streams when we need stronger delivery guarantees/visibility.
6. Priority lanes: Not needed for v1.
7. `reply_to`: queue name only (no webhooks in v1).
8. Image references: use full URIs in image_refs[i].value(e.g.,s3:///).
9. Tracking: Keep using the existing `RecipeParseJob` table for status tracking in v1 (no new workflows table yet).
10. If OCR service is unavailable at enqueue-time: enqueue anyway; OCR workers will process when available (with retries). Recipes can mark as failed only after timeout/max-attempts.
11. `workflow_id`: use `RecipeParseJob.id` as the workflow identifier in v1.
12. Per-image error reporting: each `results[i]` includes `error` (null or {code,message}) so partial failures are debuggable without artifact storage.

## Notes
- Direct-to-OCR enqueue for image requests is preferred because the request type is known at the API boundary.
- Keep messages small. Store large OCR text as artifacts and pass references.

## Implementation Notes

All questions have been resolved. The implementation uses:
- `image_refs[]` array with explicit `index` for multi-image OCR jobs
- Full URIs (e.g., `s3://bucket/key`) in `image_refs[i].value`
- `results[]` array in `ocr.completed`, aligned by index
- `tier` field is informational only (metrics/logging)
- OCR validity is determined centrally by a lightweight LLM
- Each `results[i]` includes an `error` field (null on success; {code,message} on per-image failure).