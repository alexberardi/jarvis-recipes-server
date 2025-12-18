# Recipe Parse Queue — Extended Statuses, Mailbox, and Lifecycle PRD

This PRD defines how the recipe parse job queue in `jarvis-recipes-server` will support extended statuses (`CANCELED`, `COMMITTED`, `ABANDONED`), a mailbox-style listing for completed jobs, and a 3-day expiration/cleanup policy.

It builds on the existing queue implementation used by `POST /recipes/parse-url/async` and `GET /recipes/parse-url/status/{job_id}`.

---

## 1. Current model and behavior (baseline)

Existing SQLAlchemy model:

```py
class RecipeParseJob(Base):
    __tablename__ = "recipe_parse_jobs"

    id = Column(String, primary_key=True, index=True)
    job_type = Column(String, nullable=False)  # e.g., "url", "ocr", "social"
    url = Column(String, nullable=True)
    use_llm_fallback = Column(Boolean, nullable=False, default=True)
    status = Column(String, nullable=False, default="PENDING")  # PENDING|RUNNING|COMPLETE|ERROR
    result_json = Column(JSON)
    error_code = Column(String)
    error_message = Column(Text)
    attempts = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
```

**Current behavior (simplified):**

- `POST /recipes/parse-url/async` creates a `RecipeParseJob` with:
  - `status = "PENDING"`
  - `job_type = "url"`
  - `url`, `use_llm_fallback`, etc.
- A background worker transitions jobs:
  - `PENDING → RUNNING` when work starts (sets `started_at`).
  - `RUNNING → COMPLETE` on success (sets `completed_at`, stores `result_json`).
  - `RUNNING → ERROR` on failure (sets `error_code`, `error_message`, `completed_at`).
- `GET /recipes/parse-url/status/{job_id}` returns the job status and (for COMPLETE) the parsed result.

This PRD **extends** this behavior without breaking existing flows.

---

## 2. New/extended statuses and semantics

We will extend `status` to support the following values:

- `PENDING` — Job created and waiting in the queue.
- `RUNNING` — Worker is currently processing the job.
- `COMPLETE` — Worker finished successfully and `result_json` contains a parsed recipe/result payload. No user action yet.
- `ERROR` — Worker failed permanently. `error_code` and `error_message` are populated.
- `CANCELED` — Job was explicitly canceled by the user **before** completion; the worker should not perform any more work for this job.
- `COMMITTED` — The parsed result was used to create a persisted recipe (user pressed **Save** in the client). The job is now historical only.
- `ABANDONED` — The job completed successfully, but the result was never used. After a configurable timeout (in minutes) without user interaction, a background cleanup job marks it as ABANDONED. This timeout is controlled by an environment variable (see section 8).

### 2.1. Who triggers what

- **Server / worker automatically sets:**
  - `PENDING` (on creation).
  - `RUNNING` (when a worker starts).
  - `COMPLETE` (on successful parse).
  - `ERROR` (on permanent failure).

- **User-triggered via API:**
  - `CANCELED` — user explicitly cancels an in-progress job (via a cancel endpoint).
  - `COMMITTED` — user saves a recipe derived from the job (via recipe creation endpoint, see section 5).

- **Background cleanup job sets:**
  - `ABANDONED` — after 3 days in `COMPLETE` without being committed or canceled.

---

## 3. Model changes

We will extend the `recipe_parse_jobs` table to support user ownership, lifecycle timestamps, and the new statuses.

### 3.1. New fields

Add the following columns:

```py
user_id = Column(String, nullable=False, index=True)  # FK to users table (or equivalent user identifier)

# Lifecycle timestamps (optional but recommended for debugging/reporting)
committed_at = Column(DateTime, nullable=True)
abandoned_at = Column(DateTime, nullable=True)
canceled_at = Column(DateTime, nullable=True)
```

Notes:

- `user_id` links each job to the user that initiated it. This is required for mailbox/job listing and for authorization checks on status, cancel, and commit.
- `committed_at` is set when the job becomes `COMMITTED`.
- `abandoned_at` is set by the cleanup job when the job becomes `ABANDONED`.
- `canceled_at` is set when the job becomes `CANCELED`.

### 3.2. Indexes

Add indexes to support common queries:

- `(user_id, status)` — for listing jobs by user and status (e.g., mailbox for `COMPLETE`).
- `completed_at` — for cleanup job filtering (older than 3 days).

### 3.3. Status enum (optional)

We can either keep `status` as a `String` or convert to a database enum. For now, we will keep it as `String` but **define the allowed values in code**:

```py
class RecipeParseJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    CANCELED = "CANCELED"
    COMMITTED = "COMMITTED"
    ABANDONED = "ABANDONED"
```

---

## 4. Status transitions (state machine)

The lifecycle of a job is a state machine with controlled transitions.

### 4.1. State diagram (conceptual)

- `PENDING → RUNNING` (worker starts)
- `RUNNING → COMPLETE` (worker success)
- `RUNNING → ERROR` (worker failure)
- `PENDING → CANCELED` (user cancels before worker picks up)
- `RUNNING → CANCELED` (user cancels while worker is processing)
- `COMPLETE → COMMITTED` (user saves recipe using job result)
- `COMPLETE → ABANDONED` (cleanup job after 3 days of inactivity)

Once a job is in `ERROR`, `CANCELED`, `COMMITTED`, or `ABANDONED`, it is considered **terminal** and should not be transitioned further.

### 4.2. Concurrency rules

To avoid race conditions between the worker and user actions:

- Updates should be done with **conditional checks** on the current status, e.g.:
  - When setting `COMMITTED`, only allow if current `status == COMPLETE`.
  - When setting `CANCELED`, only allow if current `status in {PENDING, RUNNING}`.
  - When setting `ABANDONED`, only allow if current `status == COMPLETE` and `completed_at` older than 3 days.
- Worker should avoid overwriting terminal statuses:
  - When a worker tries to set `RUNNING` or `COMPLETE`, it should first check that the status is not already `CANCELED`, `COMMITTED`, or `ABANDONED`.

---

## 5. Integration with recipe creation (COMMITTED)

When the client opens a completed job and then saves a new recipe based on that data, we want to:

1. Create the recipe (existing behavior).
2. Mark the associated job as `COMMITTED`.

### 5.1. API shape

Extend the **recipe creation request** (`POST /recipes`) to optionally accept `parse_job_id`:

```json
{
  "title": "...",
  "description": "...",
  "... other fields ...": "...",
  "parse_job_id": "uuid-job-id"  // optional
}
```

Behavior:

- If `parse_job_id` is omitted or `null` → behave as today (no job linkage).
- If `parse_job_id` is provided:
  - Server should:
    1. Look up the job by `id` and `user_id` (must match the authenticated user).
    2. Validate that `status == COMPLETE` (and optionally that `result_json` is present).
    3. Proceed with recipe creation.
    4. On successful recipe creation:
       - Set `status = COMMITTED`.
       - Set `committed_at = now()`.
    5. On failure to create recipe (validation/DB error):
       - Leave the job in `COMPLETE` and return appropriate error to the client.

### 5.2. Response

`POST /recipes` response can optionally include the `parse_job_id` it committed, but this is not strictly required. The main linkage is from job → recipe via status and timestamps.

---

## 6. Cancel endpoint (CANCELED)

In the future, the mobile client may present a "Cancel" action that truly cancels a job server-side (distinct from simply navigating away from the status screen).

Add a new endpoint:

### 6.1. Endpoint definition

- **POST** `/recipes/parse-url/jobs/{job_id}/cancel`
- **Auth**: Bearer JWT

**Request body:**

- No body required initially.

**Behavior:**

- Look up the job by `id` and `user_id` (must be owned by the authenticated user).
- If job not found → `404`.
- If `status` is in `{ERROR, COMPLETE, COMMITTED, ABANDONED, CANCELED}` → return `409 Conflict` with a message indicating the job cannot be canceled.
- If `status` is in `{PENDING, RUNNING}`:
  - Set `status = CANCELED`.
  - Set `canceled_at = now()`.
  - Optionally clear `result_json` if any partial result exists (not required).

**Response 200:**

```json
{
  "id": "uuid-job-id",
  "status": "CANCELED"
}
```

**Worker cooperation:**

- Worker implementations should check the current `status` before starting or finishing a job:
  - If the job is `CANCELED`, the worker should not perform work (or should discard any partial result).

---

## 7. Mailbox / job listing endpoint

To support the mailbox UI in the mobile app, we need an endpoint that lists jobs awaiting user action.

### 7.1. Endpoint definition

- **GET** `/recipes/parse-url/jobs`
- **Auth**: Bearer JWT

**Query parameters:**

- `status`: optional, defaults to `COMPLETE`.
  - For mailbox usage, the primary use case is `status=COMPLETE` to list jobs that:
    - finished successfully
    - have not yet been COMMITTED, CANCELED, or ABANDONED.
- `include_expired`: optional boolean, default `false`.
  - If `false`, exclude jobs whose `completed_at` is older than 3 days or whose status is not `COMPLETE`.

**Server-side filtering (mailbox default):**

When called with default parameters (no `status`, `include_expired=false`):

- Return jobs where:
  - `user_id == current_user.id`
  - `status == COMPLETE`
  - `completed_at >= now() - abandon_timeout_minutes`

Where `abandon_timeout_minutes` is derived from the same environment-driven timeout described in section 8.

**Response shape:**

```json
{
  "jobs": [
    {
      "id": "uuid-job-id",
      "job_type": "url",
      "url": "https://example.com/recipe",
      "status": "COMPLETE",
      "completed_at": "2025-12-09T12:34:56Z",
      "warnings": ["LLM fallback used; please verify ingredients."] ,
      "preview": {
        "title": "Short Title from result_json.recipe.title",
        "source_host": "example.com"
      }
    }
  ]
}
```

Notes:

- `warnings` and `preview` are derived from `result_json`:
  - `warnings` → from the parse result.
  - `preview.title` → likely `result_json.recipe.title`.
  - `preview.source_host` → parsed from `url`.
- The full `result_json` is **not** returned in the listing to keep payload small. The client will fetch the full result via `GET /recipes/parse-url/status/{job_id}` when the user taps a specific job.

---

## 8. Cleanup job (ABANDONED)

We need a background process that marks completed-but-unused jobs as ABANDONED after 3 days, and optionally prunes their stored result.

### 8.1. Policy

- A job should be marked `ABANDONED` when:
  - `status == COMPLETE`, and
  - `completed_at < now() - abandon_timeout_minutes`

The value of `abandon_timeout_minutes` must be read from configuration, for example an environment variable such as `RECIPE_PARSE_JOB_ABANDON_MINUTES`:

- Type: integer (minutes)
- Default: 4320 (3 days)
- During local development/testing, this can be set to a much smaller value (e.g., 5 or 10 minutes) to exercise the cleanup logic quickly.

### 8.2. Implementation sketch

Implement a periodic task (e.g., a scheduled job or a cron-like background task in the server) that runs at least once per day:

1. Find jobs where:
   - `status == COMPLETE`
   - `completed_at < now() - abandon_timeout_minutes`
2. For each match:
   - Set `status = ABANDONED`.
   - Set `abandoned_at = now()`.
   - Optionally truncate `result_json` if storage is a concern (could keep only minimal metadata).

To maintain correctness, use conditional updates to ensure we do not race with a commit event:

- Only update rows that **still** have `status == COMPLETE` at the time of the update.

---

## 9. Status endpoint updates

We will keep the existing `GET /recipes/parse-url/status/{job_id}` endpoint but extend the allowed `status` values and ensure the response reflects the new lifecycle.

### 9.1. Status response shape

Example response:

```json
{
  "id": "uuid-job-id",
  "status": "PENDING" | "RUNNING" | "COMPLETE" | "ERROR" | "CANCELED" | "COMMITTED" | "ABANDONED",
  "result": {
    "success": true,
    "recipe": { /* ParsedRecipe payload */ },
    "used_llm": false,
    "parser_strategy": "schema_org_json_ld",
    "warnings": []
  },
  "error_code": null,
  "error_message": null
}
```

Rules:

- `result` is populated only when there is a meaningful parse result:
  - Typically when `status == COMPLETE`.
  - It may optionally remain populated for `COMMITTED`/`ABANDONED` for debugging/history, but clients should not rely on this.
- For `ERROR`, `error_code` and `error_message` should be populated.
- For `CANCELED`, `COMMITTED`, `ABANDONED`, `result` may be omitted or left as in the last known state (implementation choice). The mobile client will primarily act on `status` and, in the case of `COMPLETE`, the `result`.

---

## 10. Authorization

All queue-related endpoints must be scoped to the authenticated user:

- `GET /recipes/parse-url/status/{job_id}` should only return jobs where `user_id == current_user.id`.
- `POST /recipes/parse-url/jobs/{job_id}/cancel` should only operate on jobs owned by the current user.
- `GET /recipes/parse-url/jobs` should only list jobs for the current user.
- When creating a job (`POST /recipes/parse-url/async`), the `user_id` must be populated from the authenticated user context.

---

## 11. Implementation notes / ordering

1. **DB migration:**
   - Add `user_id`, `committed_at`, `abandoned_at`, `canceled_at` columns.
   - Backfill `user_id` for any existing jobs if possible (or leave null for legacy rows and guard accordingly).
   - Add indexes on `(user_id, status)` and `completed_at`.

2. **Status enum and model refactor:**
   - Define `RecipeParseJobStatus` enum in Python and use it consistently.
   - Update worker and queue logic to avoid overwriting terminal statuses.

3. **Integrate user_id:**
   - Set `user_id` on job creation using the current authenticated user.
   - Update all job fetches to filter by `user_id`.

4. **Extend `/status/{job_id}`:**
   - Return the expanded set of statuses.

5. **Integrate COMMITTED via recipe creation:**
   - Extend `RecipeCreate` schema with optional `parse_job_id`.
   - After successful recipe creation, set job status to `COMMITTED`.

6. **Mailbox endpoint:**
   - Implement `GET /recipes/parse-url/jobs` with filters described above.

7. **Cleanup job:**
   - Implement a scheduled task marking stale `COMPLETE` jobs as `ABANDONED`.

8. **Cancel endpoint (optional / phased):**
   - Implement `POST /recipes/parse-url/jobs/{job_id}/cancel` when ready to expose real cancellation in the client.

This ordering allows us to ship COMMITTED/ABANDONED semantics and mailbox support first, and then add real cancellation behavior when the client is ready to surface it.
