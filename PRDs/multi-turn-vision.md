

# Multi-turn Vision Processing (Sequential Images via LLM Proxy)

## Goal
Enable **multi-image recipe extraction** without requiring a vision model that supports true multi-image attention.

We will:
- Accept **1–8 images** from the client.
- Process **one image at a time** through the **LLM Proxy** vision route (as used today).
- Maintain **server-side state** (an evolving `RecipeDraft` JSON) across turns.
- On the final image, request a **final consolidated draft**.
- Optionally run a **text-only normalization pass** using the standard “full” text model to polish/validate output.

This avoids high RAM usage from multi-image in a single request and works with MLX/single-image backends.

## Non-Goals
- True multi-image attention in a single model invocation.
- Streaming partial responses to the client per image (client remains mailbox/job-based).
- Remote GPT integrations (can be added later as an additional tier).

## Assumptions
- Server already talks to `jarvis-llm-proxy-api` for vision and text parsing.
- We already have an async job + mailbox flow for “recipe by image” (ingestion + worker).
- Images are uploaded to S3 (private) and referenced by S3 keys.

## Key Concepts

### Multi-turn (Sequential) Vision
We simulate “consider all images” by:
- Running **N single-image calls**.
- Passing the **current draft JSON** back to the model each turn.
- Instructing the model to **update** the draft only with evidence from the current image.

### Server-owned State
The model does not retain memory between requests. The server stores:
- `current_draft` (JSON)
- `turn_index` / `image_index`
- extracted evidence/debug info per turn

## API / Job Flow

### Client-facing endpoint (unchanged semantics)
- `POST /recipes/from-image/jobs` (multipart)
  - Validates 1–8 images.
  - Uploads images to S3.
  - Creates `recipe_ingestions` row.
  - Enqueues `recipe_parse_jobs` with `job_type="image"` and job_data including `ingestion_id`, `s3_keys`, `tier_max`, `title_hint`.
  - Returns `202 { ingestion_id }`.

### Worker behavior (this PRD)
For `job_type="image"`:
1) Download images (or stream) from S3.
2) Run Tier 1/2 OCR gates first (Tesseract / Tesseract++). If OCR quality is sufficient, skip vision.
3) If vision is required:
   - Run sequential vision calls **per image** using the LLM Proxy.
   - Maintain `current_draft` JSON between calls.
4) Produce final `recipe_draft` and post mailbox message:
   - `recipe_image_ingestion_completed`
   - or `recipe_image_ingestion_failed`

## Data Model

### recipe_ingestions (existing/expected)
Must store:
- `id`
- `user_id`
- `status` (PENDING|PROCESSING|COMPLETED|FAILED)
- `image_s3_keys` (array)
- `tier_max`
- `selected_tier`
- Tier outputs/metrics JSON (ocr texts, scores, etc.)
- `pipeline_json` (debug trace)
- `recipe_draft_json` (final draft)
- timestamps

### recipe_parse_jobs (existing)
Add/ensure `job_data` JSON contains:
- `ingestion_id`
- `s3_keys` (array)
- `tier_max` (optional)
- `title_hint` (optional)

### mailbox_messages (existing/expected)
On completion, publish:
- `type: "recipe_image_ingestion_completed"`
- `payload` includes `{ ingestion_id, recipe_draft, pipeline }`

On failure:
- `type: "recipe_image_ingestion_failed"`
- `payload` includes `{ ingestion_id, error_code, message, pipeline? }`

## LLM Proxy Contract

### Model name routing
- Vision calls:
  - Use env `JARVIS_VISION_MODEL_NAME`, default `"vision"`.
- Text-only normalization (optional):
  - Use env `JARVIS_FULL_MODEL_NAME`, default `"full"`.

### Timeouts
Vision can take ~20s/image.
- Set a high request timeout (e.g., **120–180s**) per image call.

### Per-image request shape
Use the existing OpenAI-style `/chat/completions` or `/responses` wrapper used by the server today.

We will send:
- A stable **system prompt**.
- A **user message** containing:
  - `current_draft` JSON
  - `image_index`, `image_count`
  - `is_final_image`
  - Any `title_hint`
- A single image attachment.

## Prompting

### Output format
The vision model must return **ONLY** valid JSON for `RecipeDraft`.
No markdown. No commentary.

### RecipeDraft schema (server-side contract)
Minimum shape (can match existing server schema if already defined):
```json
{
  "title": "string|null",
  "yield": "string|null",
  "total_time": "string|null",
  "ingredients": [
    {"text": "string", "section": "string|null"}
  ],
  "steps": [
    {"text": "string", "section": "string|null"}
  ],
  "notes": ["string"],
  "source": {
    "type": "image",
    "title_hint": "string|null"
  }
}
```

### System prompt (use for every image turn)
- You are extracting a cooking recipe from images.
- You will receive `current_draft` JSON and **one** image.
- Update the draft using **only** information visible in the image.
- Preserve existing fields unless contradicted by clear evidence.
- Deduplicate ingredients/steps.
- Return **ONLY** valid JSON matching the schema.

### User message template (per image)
Include:
- `current_draft`
- `image_index` / `image_count`
- `is_final_image`
- `title_hint` if provided

On the final image set `is_final_image=true` and instruct consolidation.

## Merge Strategy (Server-side)
Two supported approaches:

### Approach A (recommended): Model returns updated full draft
Each turn:
- Server sends `current_draft`.
- Model returns a full updated draft.
- Server validates JSON, normalizes, stores as new `current_draft`.

Pros: simplest.
Cons: model may rewrite/oscillate.

### Approach B (future/optional): Model returns patch/deltas
Each turn returns:
- `ingredients_add[]`, `steps_add[]`, `field_updates{}`
Server merges deterministically.

Pros: more stable.
Cons: more implementation.

**This PRD implements Approach A.**

## Validation & Quality Gates

### OCR-first
Before vision:
- Run OCR tier(s) and evaluate with heuristics.
- If OCR result passes quality threshold, produce draft using text model and skip vision.

### Vision JSON validation
- Strict JSON parse.
- Validate required fields/types.
- If invalid, retry once with a “repair” prompt.
- If still invalid, mark ingestion failed.

### Dedup rules (lightweight)
- Ingredient dedup: normalize whitespace/case; remove exact duplicates.
- Step dedup: remove exact duplicates; preserve order.

## Error Handling
- If any per-image vision call fails:
  - Retry that image once.
  - If still fails, set ingestion `FAILED` and emit mailbox failure.
- If the worker memory balloons (common with MPS/Transformers VLMs on macOS):
  - Run vision inference in a **short-lived subprocess** per image.
  - The subprocess must exit after completing a single-image vision call so unified memory is reclaimed by the OS.
  - If the subprocess crashes/OOMs, treat it as a per-image failure and apply the normal retry policy (retry once, then fail ingestion).


## Performance Considerations
- Downscale images before vision (max 1024px on long edge; consider 768px if memory constrained).
- Limit to **8 images**.
- Keep `current_draft` compact (JSON only; do not accumulate raw OCR history in the prompt).

## Subprocess Vision Runner

### Rationale
On Apple Silicon, PyTorch/MPS frequently retains large allocations even after inference completes. In a long-lived worker process this can cause unified memory usage to climb across jobs until the machine becomes unstable.

To keep memory usage bounded, vision inference should be executed in a **separate process** that is terminated after each image.

### Execution Model
- The main worker process coordinates the job and maintains `current_draft`.
- For each image that requires vision:
  1) Spawn `vision_runner.py` (or equivalent) with arguments:
     - `--model-name` (default `vision`)
     - `--timeout-seconds` (default 180)
     - `--image-path` or `--image-base64`
     - `--payload-json` (includes `current_draft`, `image_index`, `image_count`, `is_final_image`, `title_hint`)
  2) The runner performs exactly **one** LLM Proxy request for a single image.
  3) The runner prints the resulting `RecipeDraft` JSON to stdout and exits with code 0.
  4) The main worker reads stdout, validates JSON, and updates `current_draft`.

### Failure Semantics
- Non-zero exit code, timeout, or invalid JSON from the runner is treated as a per-image vision failure.
- Retry policy:
  - Retry the same image once (spawn a fresh subprocess).
  - If it fails again, mark the ingestion FAILED and publish `recipe_image_ingestion_failed`.

### Logging / Debugging
- Runner should emit structured logs to stderr (so stdout remains JSON-only for the result).
- Main worker should store a per-image trace in `pipeline_json`:
  - `image_index`
  - whether subprocess was used
  - time taken
  - retry count
  - any stderr snippet (truncated)

### Configuration
- `JARVIS_VISION_SUBPROCESS_ENABLED` (default: `true` on macOS, `false` elsewhere)
- `JARVIS_VISION_TIMEOUT_SECONDS` (default: `180`)
- `JARVIS_VISION_SUBPROCESS_MAX_RETRIES` (default: `1`)

## Implementation Plan
1) Add sequential vision pipeline function (e.g., `run_sequential_vision(images, title_hint, tier_max) -> RecipeDraft`) that supports invoking a subprocess runner per image when enabled.
2) Integrate into `job_type="image"` worker.
3) Ensure LLM Proxy call wrapper supports:
   - attaching a single image
   - passing `model` name (`vision`)
   - high timeout
4) Persist `current_draft` per turn into `recipe_ingestions.pipeline_json` for debugging.
5) Emit mailbox message on completion/failure.

## Testing
- Unit tests:
  - 1 image → single vision call
  - N images → N calls, final consolidation
  - invalid JSON response → repair retry → fail
- Integration tests (mock LLM Proxy):
  - multi-image job enqueues and completes
  - >8 images rejected

## Open Questions
- Do we want the optional final “text-only normalization” pass enabled by default, or only when confidence is low?
- Should we store each per-image model response verbatim in `pipeline_json` for later debugging?