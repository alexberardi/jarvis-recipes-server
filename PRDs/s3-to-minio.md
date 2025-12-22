

# Recipes storage: S3 → MinIO (dev) spec

## Summary
If **Jarvis Recipes uploads images to AWS S3** but **Jarvis OCR fetches from MinIO** (or vice‑versa), OCR will fail because the referenced objects won’t exist in the target store.

To keep the system decoupled and predictable, all services in a given environment should treat **one object store as the source of truth** and communicate using **full URIs** in the queue contract.

This doc specifies how `jarvis-recipes` should support **MinIO (S3-compatible)** as its object store in dev while remaining compatible with AWS S3 in prod.

---

## Goals
- `jarvis-recipes` can store and retrieve image artifacts in **MinIO** using the **same S3 client code** path as AWS S3.
- Queue messages reference images using **full URIs** (e.g., `s3://jarvis-dev/...`) so OCR can fetch them.
- Switching between MinIO and AWS S3 is a **config change only** (env vars), not a code fork.

## Non-goals
- Migrating previously uploaded AWS S3 dev data into MinIO.
- Implementing presigned URL upload flows for mobile (future).

---

## Decision
### Single object store per environment
For v1:
- **Dev/local:** MinIO is the object store.
- **Prod (later):** AWS S3 is the object store.

All services (recipes + ocr + any future consumers) must be configured to point to the same store.

---

## Configuration
### Required env vars (recipes)
Recipes should support these env vars (matching OCR):

- `OBJECT_STORE_PROVIDER` (default: `minio` for dev; `s3` for prod)
- `S3_ENDPOINT_URL` (optional; required for MinIO)
- `S3_REGION` (default: `us-east-1`)
- `S3_FORCE_PATH_STYLE` (default: `true` for MinIO; `false` for AWS)
- `S3_BUCKET` (e.g., `jarvis-dev`)
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

### URI format (contract)
Recipes must emit URIs that OCR can resolve:

- Preferred: `s3://<bucket>/<key>`

Example:
- `s3://jarvis-dev/recipe-images/<user_id>/<ingestion_id>/0.jpg`

Notes:
- The `s3://` scheme is used for both AWS S3 and MinIO because MinIO is S3-compatible.
- The actual endpoint (AWS vs MinIO) is determined by env vars, not the URI.

---

## Storage layout
### Bucket
- `S3_BUCKET=jarvis-dev` (dev)

### Key prefix conventions
Use a stable prefix so future services can reason about the data:

- `recipe-images/<user_id>/<ingestion_id>/<index>.<ext>`

Where:
- `user_id` is the authenticated user
- `ingestion_id` is the ingestion workflow/job id
- `index` aligns with the OCR multi-image contract
- `ext` is the detected/normalized image extension (`jpg` recommended)

---

## Upload + Queue flow (image ingestion)
### Current intent
- Mobile (or client) uploads images to recipes
- Recipes persists the images to object storage
- Recipes enqueues OCR work using the URI(s)

### Updated v1 behavior (MinIO)
1. Recipes receives images via API.
2. Recipes writes each image to object storage (MinIO).
3. Recipes builds `image_refs[]` with `kind="s3"` and full `s3://...` URIs.
4. Recipes enqueues `ocr.extract_text.requested` directly to `jarvis.ocr.jobs`.

Important:
- Recipes **must not** enqueue `local_path` refs for multi-service workflows unless OCR shares the same filesystem/mount.
- Prefer object storage refs in all distributed cases.

---

## Implementation plan (recipes)

### 1) Add a tiny storage abstraction
Create a small module (names are suggestions):
- `app/storage/object_store.py`

Responsibilities:
- `put_bytes(bucket, key, content_type, bytes) -> uri`
- `get_bytes(bucket, key) -> bytes` (optional for recipes)
- `uri_for(bucket, key) -> s3://...`

Implementation:
- Use `boto3` for both AWS and MinIO.
- If `S3_ENDPOINT_URL` is set, pass it to boto3 client.
- If `S3_FORCE_PATH_STYLE=true`, configure the client accordingly.

### 2) Recipes uses object store for image persistence
Where recipes currently:
- writes images to disk, or
- uploads to AWS S3 directly without shared config,

Update it to call the object store module.

### 3) Emit URIs in queue messages
When creating the OCR request:
- set `image_refs[i].kind = "s3"`
- set `image_refs[i].value = "s3://<bucket>/<key>"`
- set `index` deterministically 0..N-1

### 4) Ensure both services share endpoint config
- In dev, both recipes and ocr must set:
  - `S3_ENDPOINT_URL=http://localhost:9000`
  - `S3_FORCE_PATH_STYLE=true`
  - `S3_BUCKET=jarvis-dev`

---

## Docker / local dev setup
### Add MinIO to centralized compose (recommended)
In your `jarvis-data-stores` compose, add:

```yaml
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      MINIO_ROOT_USER: jarvis
      MINIO_ROOT_PASSWORD: jarvis-dev-password
    volumes:
      - minio_data:/data

volumes:
  minio_data:
```

Then:
- Console: `http://localhost:9001`
- API: `http://localhost:9000`

Create bucket:
- `jarvis-dev`

### Recipes .env (dev)

```env
OBJECT_STORE_PROVIDER=minio
S3_ENDPOINT_URL=http://localhost:9000
S3_REGION=us-east-1
S3_FORCE_PATH_STYLE=true
S3_BUCKET=jarvis-dev
AWS_ACCESS_KEY_ID=jarvis
AWS_SECRET_ACCESS_KEY=jarvis-dev-password
```

### OCR .env (dev)
Use the same values.

---

## Validation / acceptance criteria
- Upload 2 images via recipes ingestion endpoint.
- Confirm objects exist in MinIO console under:
  - `recipe-images/<user_id>/<ingestion_id>/0.jpg`
  - `recipe-images/<user_id>/<ingestion_id>/1.jpg`
- Confirm recipes enqueues `ocr.extract_text.requested` with:
  - `image_refs[].value` using `s3://jarvis-dev/...`
- Confirm OCR worker fetches both images from MinIO and returns `ocr.completed`.

---

## Resolved decisions (v1)
1. **Image format normalization (RESOLVED)**: Normalize all uploaded images to `.jpg` on ingestion.
   - Benefits:
     - Predictable downstream OCR behavior
     - Smaller, more consistent file sizes
     - Simplified content-type handling across services
   - Tradeoff: original format is not preserved in v1 (acceptable for recipes OCR use case).
2. **image_refs.kind value (RESOLVED)**: Always emit `kind="s3"`.
   - Rationale: MinIO is S3-compatible; endpoint differences are handled entirely via configuration.
   - This keeps the queue contract stable and avoids leaking infrastructure details into messages.
