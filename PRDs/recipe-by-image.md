# PRD: Add Recipe by Image (Tiered OCR + Vision)

## Overview

Add a new end-to-end feature that lets a user create a recipe by uploading **one or more images** (photos or screenshots). The server will process the image through a **tiered pipeline**:

- **Tier 1:** Local OCR with **Tesseract** (fast, cheap)
- **Tier 2:** EasyOCR (accuracy-focused)
- **Tier 3:** **Vision LLM via llm-proxy** (highest quality extraction + structuring)

Remote GPT/hosted OCR is explicitly **out of scope** for v1.

This PRD focuses on server-side implementation; frontend can be minimal for v1.

---

## Goals

1. Support creating a recipe from an uploaded image.
2. Use a tiered approach that escalates only when needed.
3. Store:
   - the original image
   - intermediate OCR text
   - the final structured recipe
   - per-tier diagnostics (confidence/quality, timings, failures)
4. Make the pipeline deterministic and debuggable (clear logs, artifact retention).
5. Ensure the feature works fully locally.

---

## Non-goals

- Remote GPT (OpenAI) vision/OCR.
- Perfect parsing of every cookbook layout; escalation should handle hard cases.
- Fully polished UX; minimal UI is fine.

---

## User Stories

1. As a user, I can take a photo of a recipe page and import it into Jarvis Recipes.
2. As a user, I can review the extracted recipe and edit fields before saving.
3. As a developer, I can see which tier succeeded, how long it took, and what text was extracted.

---

## System Design

### High-level flow

1. Client uploads one or more images.
2. Server runs Tier 1 OCR on each image → yields `raw_text_1[]` + per-image metrics.
3. Server checks quality gates.
   - If good enough: proceed to structuring (LLM text-only) and save recipe.
   - If not: Tier 2.
4. Tier 2 OCR on each image → yields `raw_text_2[]` + per-image metrics.
5. Quality gates again.
   - If good enough: structuring and save.
   - If not: Tier 3.
6. Tier 3 uses **llm-proxy vision** directly on the image.
   - Returns structured recipe.
7. Combine extracted text in upload order into a single `combined_text` (with page separators) and proceed to text-only structuring. (Tier 3 is vision; see below for multi-image handling.)
8. Store artifacts and return a created recipe.

### Tier definitions

#### Tier 1: Tesseract (baseline)

- Runs locally, fast.
- Produces plain text.

Recommended preprocessing steps (keep simple for v1):
- Normalize orientation if EXIF rotation present.
- Convert to grayscale.
- Apply mild contrast/threshold.

Output:
- `raw_text`
- OCR diagnostics:
  - mean word confidence (if available)
  - character count
  - time spent

#### Tier 2: EasyOCR (accuracy-focused)

Goal: improve OCR on messy lighting, skew, small fonts, and phone photos where Tesseract struggles.

Implementation:
- Use **EasyOCR** as the secondary OCR engine.
- Run after Tier 1 fails quality gates.
- Minimal preprocessing is acceptable for v1; EasyOCR is generally robust without heavy tuning.

Output:
- `raw_text`
- diagnostics:
  - detected language(s)
  - character count
  - time spent

#### Tier 3: Vision LLM via llm-proxy

- Send the image (base64 data URL) to llm-proxy’s OpenAI-compatible `/v1/chat/completions`.
- Use the **vision model** (alias `vision` or concrete id) and a constrained prompt to return structured JSON.

Output:
- Structured `RecipeDraft` (JSON)
- Optionally also return extracted text (if prompt includes it)

---

## API Design

### Endpoint: Create recipe from image

**POST** `/recipes/from-image`

Request:
- `multipart/form-data`
  - `images`: one or more files (same field name repeated)
  - Optional fields:
    - `title_hint`: string
    - `tier_max`: int (1–3) default 3
    - `dry_run`: bool default false (runs pipeline, returns draft, doesn’t persist recipe)

Images are processed in the order received.

Response (success):
- `201 Created`
- Body:

```json
{
  "recipe": { /* normal recipe response */ },
  "pipeline": {
    "selected_tier": 2,
    "image_count": 2,
    "attempts": [
      {
        "tier": 1,
        "status": "failed_quality" ,
        "duration_ms": 812,
        "metrics": {"char_count": 340, "confidence": 41},
        "error": null
      },
      {
        "tier": 2,
        "status": "success",
        "duration_ms": 1750,
        "metrics": {"char_count": 2100, "confidence": 67},
        "error": null
      }
    ]
  }
}
```

Response (dry_run):
- `200 OK`
- returns `recipe_draft` instead of persisted recipe.

Errors:
- `400` invalid image / missing file
- `413` file too large
- `422` could not extract recipe (all tiers failed)
- `502` llm-proxy unavailable (when tier 3 attempted)

### Endpoint: Get pipeline artifacts (optional v1)

**GET** `/recipes/from-image/jobs/{job_id}`

If we implement async jobs later, this endpoint returns status + artifacts.

For v1 we can keep the pipeline synchronous and omit this.

---

## Data Model

Add a lightweight table to retain ingestion artifacts.

### `recipe_ingestions`

Fields (suggested):
- `id` (uuid)
- `user_id`
- `created_at`
- `image_s3_keys` (jsonb array of strings)
- `selected_tier` (int)
- `tier1_text` (nullable)
- `tier2_text` (nullable)
- `tier3_raw_response` (nullable, store minimal)
- `pipeline_json` (jsonb) – attempts array + metrics
- `status` (success/failed)

A dedicated ingestion table **must** be created for v1.

Rationale:
- Ingestion artifacts are not part of the recipe domain model.
- Failed or dry-run ingestions still need to be retained.
- Tier diagnostics, OCR text, and vision responses should not pollute the recipes table.

Notes:
- `pipeline_json` should store the full attempts array, timings, metrics, and escalation decisions.
- `tier*_text` fields may be truncated if excessively large; raw images remain the source of truth.
- `created_at` / `updated_at` timestamps should be included.

---

## Image Storage (S3)

Uploaded recipe images are stored in Amazon S3. Images are treated as ingestion artifacts and are not publicly accessible.

### Configuration and failure behavior

S3 configuration is provided exclusively via environment variables:

- `RECIPE_IMAGE_S3_BUCKET`
- `RECIPE_IMAGE_S3_REGION`
- `RECIPE_IMAGE_S3_PREFIX`
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

Behavior:
- All S3 variables are **required** when this feature is enabled.
- If any required S3 configuration is missing or invalid, the request must **fail fast** with a 5xx error.
- Local filesystem mocking or fallback storage is **not supported** in v1.

Rationale:
- Image ingestion artifacts are part of the persistent audit/debug trail.
- Silent local fallback would introduce inconsistent behavior across environments.

### Storage layout

Images are stored using a predictable, debuggable key structure:

```
{s3_prefix}/{user_id}/{ingestion_id}/{index}.{ext}
```

Example:
```
recipe-images/123e4567-e89b-12d3-a456-426614174000/9f1c2c3a-acde-4e2c-b7b0-8f5c2e9a7d11/1.jpg
recipe-images/123e4567-e89b-12d3-a456-426614174000/9f1c2c3a-acde-4e2c-b7b0-8f5c2e9a7d11/2.jpg
```

### Access model

- Objects are **private** by default.
- The server accesses images directly via the AWS SDK.
- If the client ever needs access (e.g., preview UI), the server must generate a **presigned URL** with a short TTL.

### Lifecycle considerations

- Images should be retained for debugging and audit purposes initially.
- Future optimization may include lifecycle rules to expire images after N days.

### Failure handling

- If S3 upload fails, the request should fail fast with a 5xx error.
- OCR should not run if the image cannot be safely stored.

---

## Quality Gates (Escalation Rules)

We need a deterministic decision to escalate.

### Suggested heuristics (v1)

Heuristics are evaluated on the **combined OCR text** across all images.
Per-image metrics are retained only for diagnostics.

Hard fail conditions (any → escalate):
- `char_count < 500`
- `line_count < 10`

Soft scoring signals:
- Mean confidence >= 50 → +1
- Contains >= 2 of the keywords:
  - Ingredients
  - Directions
  - Instructions
  - Method
  - Serves
  - Yield
- Ingredient-like lines detected (regex match for quantities or bullets) → +1
- Step-like lines detected (numbered or imperative sentences) → +1

Scoring:
- Start at 0
- Add points for soft signals

Decision:
- If hard fail → escalate
- Else if score >= 2 → accept tier and attempt structuring
- Else → escalate

### Structuring validation

After structuring OCR text into a RecipeDraft:

Validation rules:
- title exists and length >= 3
- ingredients count >= 3
- steps count >= 2

If validation fails:
- Escalate to the next tier (unless already at Tier 3)
- If Tier 3 fails validation, return 422

---

## LLM Usage (Text-only vs Vision)

We will use the llm-proxy for two different purposes:

1. **Text-only structuring** (after Tier 1/2)
2. **Vision extraction + structuring** (Tier 3)

### Text-only structuring prompt (Tier 1/2)

Input:
- OCR text

Output:
- JSON `RecipeDraft`

Constraints:
- Must produce valid JSON
- Must not include commentary

### Vision prompt (Tier 3)

Input:
- One or more images (all uploaded images)
- Optional OCR text as a hint (optional; can be included later)

Output:
- JSON `RecipeDraft`

#### Vision Prompt (v1 – strict)

System:
You are an expert at reading photographed or scanned recipes and converting them into structured data.
You must return **only valid JSON** that conforms exactly to the RecipeDraft schema.
Do not include explanations, markdown, or commentary.

User:
You are given one or more images of a recipe.
The images may be photographed pages, screenshots, or partial views.
They may contain noise, page numbers, ads, or formatting artifacts.

Instructions:
- Extract the recipe title, ingredients, and steps as accurately as possible.
- Preserve ingredient order as shown in the images.
- Preserve step order as shown in the images.
- If quantities or units are unclear, set them to null rather than guessing.
- If prep/cook times are not explicitly stated, set them to 0.
- If servings are unclear, set to null.
- Do not hallucinate missing ingredients or steps.
- If multiple images are provided, treat them as consecutive pages of the same recipe.

Return **only** a single JSON object matching this schema:
<RecipeDraft JSON schema>

---

## RecipeDraft Schema (server-internal)

Define a strict schema used between pipeline and persistence:

```json
{
  "title": "string",
  "description": "string | null",
  "ingredients": [
    {
      "name": "string",
      "quantity": "string | null",
      "unit": "string | null",
      "notes": "string | null"
    }
  ],
  "steps": ["string"],
  "prep_time_minutes": 0,
  "cook_time_minutes": 0,
  "total_time_minutes": 0,
  "servings": "string | null",
  "tags": ["string"],
  "source": {
    "type": "image",
    "original_filename": "string | null",
    "ocr_tier_used": 1
  }
}
```

Server should validate this schema before saving.

---

## Configuration

### Environment variables

- `RECIPE_IMAGE_MAX_BYTES` (default e.g. 10MB)
- `RECIPE_OCR_TIER_MAX` (default 3)
- `RECIPE_OCR_TESSERACT_ENABLED` (default true)
- `RECIPE_OCR_TIER2_ENABLED` (default true)
- `RECIPE_OCR_VISION_ENABLED` (default true)

llm-proxy integration:
- `LLM_PROXY_BASE_URL`
- `LLM_PROXY_MODEL_TEXT_FULL` (e.g. `full`)
- `LLM_PROXY_MODEL_VISION` (e.g. `vision`)

Auth to llm-proxy:
- Use the centralized app-to-app auth headers (forward caller headers or use service identity, depending on how you want trust to work). For v1, simplest is: jarvis-recipes calls llm-proxy with its own app identity.

# S3 image storage
RECIPE_IMAGE_S3_BUCKET=
RECIPE_IMAGE_S3_REGION=
RECIPE_IMAGE_S3_PREFIX=recipe-images

# AWS credentials (dev only; prefer IAM roles in prod)
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# Optional
RECIPE_IMAGE_S3_PRESIGN_TTL_SECONDS=3600

---

## Implementation Plan

1. Add upload endpoint `/recipes/from-image` with multipart parsing.
2. Add image storage via S3 (upload image before OCR begins).
3. Implement OCR Tier 1 module:
   - preprocess + Tesseract call
   - return text + metrics
4. Implement OCR Tier 2 module:
   - EasyOCR call
   - return text + metrics
5. Implement text-only structuring call via llm-proxy.
6. Implement Tier 3 vision call via llm-proxy.
7. Add quality gates and tier escalation.
8. Add ingestion artifact storage.
9. Add minimal tests:
   - image upload validation
   - tier gating logic
   - stubbed llm-proxy responses

---

## Testing

- Unit tests
  - Quality scoring and escalation
  - Draft validation
- Integration tests
  - Tier 1 success flow
  - Tier 1 fail → Tier 2 success
  - Tier 1+2 fail → Tier 3 success (mock llm-proxy)
  - llm-proxy unavailable → graceful failure (502)

---

## Open Questions

1. What is Tier 2 exactly in your earlier plan?
   - If you already have a preferred “intense OCR” component (e.g., a library or service), we should name it explicitly.
2. Do we want to store images on disk, in DB, or via an existing object store?
3. Should the endpoint be synchronous (v1) or async job-based?
4. Multi-image support (front/back, multiple pages)
   - **Answered (v1):** Support multi-image uploads via `images` and process in order. Combine OCR text with page separators before structuring; Tier 3 sends all images to vision in a single request when possible.
