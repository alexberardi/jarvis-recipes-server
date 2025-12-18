## Overview

Refactor the "Create Recipe via Image" flow to use the existing **mailbox / background job** system.

This PRD assumes an existing mailbox system is already present. The implementation must reuse the existing mailbox infrastructure rather than introducing a parallel messaging mechanism. If no mailbox exists yet, a simple mailbox table may be introduced, but its API and semantics must match the existing client polling pattern.

Image ingestion, OCR, and vision processing are long‑running tasks and should not block a single HTTP request. Instead, image uploads will enqueue a background job, and clients will poll via the mailbox system. When processing completes, the extracted `recipe_draft` will be delivered as a mailbox message, allowing the client to open the existing recipe create/edit screen pre‑filled with parsed data.

## High‑level Flow

1. Client uploads 1–8 images and submits a "recipe image ingestion" job.
2. Server stores images in S3 and enqueues a background job.
3. Background worker executes tiered OCR + vision pipeline.
4. On completion:
   - Success: mailbox message contains `recipe_draft` + ingestion metadata.
   - Failure: mailbox message contains failure reason and retry guidance.
5. Client polls mailbox and, on success, opens the existing recipe create/edit screen with the draft pre‑filled.

## Ingestion Status Lifecycle

Each `recipe_ingestions` record must transition through explicit states:

- `PENDING` – created, waiting to be processed
- `RUNNING` – worker has started processing
- `SUCCEEDED` – recipe draft successfully extracted
- `FAILED` – extraction failed

Status transitions must be persisted and reflected in mailbox messages.

## API Design

### Submit image ingestion job

POST /recipes/from-image/jobs

Request (multipart/form-data):
- `images`: repeated file field (1–8 images)
- optional: `title_hint`
- optional: `tier_max` (default 3)

Response:
- `202 Accepted`

The response does not contain the extracted recipe. Results are delivered asynchronously via the mailbox system.

### Job payload

The enqueued job payload (`job_data`) should be minimal and contain only:
- `ingestion_id`
- (optionally) `user_id`, if required by the job runner

All other data (S3 keys, tier_max, title_hint, status) must be loaded from the `recipe_ingestions` table using `ingestion_id`.

The `ingestion_id` is the primary identifier and must be returned in the 202 response and included in all mailbox messages. Do not introduce a separate `job_id` for client-facing flows.

## Mailbox Messages

### Success message

```
{
  "type": "recipe_image_ingestion_completed",
  "ingestion_id": "uuid",
  "recipe_draft": { ... },
  "pipeline": { ... }
}
```

### Failure message

```
{
  "type": "recipe_image_ingestion_failed",
  "ingestion_id": "uuid",
  "error_code": "ocr_failed | vision_failed | invalid_images",
  "message": "Human‑readable failure reason"
}
```

## Timeouts & Reliability

- The job execution time is unbounded from the client’s perspective.
- Vision processing may take ~20 seconds per image.
- Client HTTP timeouts are no longer relevant after job submission.
- Retries should be handled by resubmitting a new job.
- Worker calls to llm-proxy for vision must use a configurable long timeout (recommended default: 60–120 seconds).

### Worker filesystem safety

If OCR or vision libraries require local file access:
- Create a unique temporary directory per ingestion (e.g. `/tmp/ingestions/{ingestion_id}`)
- Ensure cleanup after job completion or failure
- Avoid filename collisions across concurrent jobs
