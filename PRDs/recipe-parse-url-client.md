# Recipe Parse via URL — Client Integration PRD

Scope: define how clients (e.g., React Native app) submit a recipe URL for parsing, handle async job status, and consume the parsed recipe payload for preview and optional save.

Server already implemented; this PRD specifies expected requests/responses, models, and UX/error handling guidance.

## Endpoints

### 1) Enqueue parse (async)
- **POST** `/recipes/parse-url/async`
- **Auth**: Bearer JWT (same as other protected routes)
- **Body**
  ```json
  {
    "url": "https://example.com/recipe",
    "use_llm_fallback": true
  }
  ```
- **Success 200**
  ```json
  {
    "id": "uuid-job-id",
    "status": "PENDING"
  }
  ```
  - `status`: `PENDING` or `RUNNING` initially.
- **Error**
  - 401/403 on auth failure
  - 422 on invalid body
  - 500 unexpected (rare)

### 2) Poll job status
- **GET** `/recipes/parse-url/status/{job_id}`
- **Auth**: Bearer JWT
- **Success 200**
  ```json
  {
    "id": "uuid-job-id",
    "status": "PENDING" | "RUNNING" | "COMPLETE" | "ERROR",
    "result": {
      "success": true,
      "recipe": { ...ParsedRecipe... },
      "used_llm": false,
      "parser_strategy": "schema_org_json_ld",
      "warnings": []
    },
    "error_code": null,
    "error_message": null
  }
  ```
  - `result` is present only on `COMPLETE`.
  - On `ERROR`, `error_code`/`error_message` populated; `result` is null.
- **Error**
  - 404 if job_id not found (e.g., expired/typo)

### 3) Existing synchronous preview (optional)
- **POST** `/recipes/parse-url`
- Blocks until parse completes; same `ParseUrlResponse` shape.
- Prefer async flow for mobile to avoid timeouts; keep sync only for internal tools or if UI wants immediate fallback.

## Models

### ParsedRecipe (from server)
```json
{
  "title": "string",
  "description": "string|null",
  "source_url": "string|null",
  "image_url": "string|null",
  "tags": ["string"],
  "servings": number|null,
  "estimated_time_minutes": number|null,
  "ingredients": [
    {
      "text": "ingredient name only",
      "quantity_display": "string|null",   // normalized fractions: e.g., "1/2"
      "unit": "string|null"                // only recognized units (cup, tsp, g, etc.)
    }
  ],
  "steps": ["string"],
  "notes": ["string"]
}
```

### ParseResult (inside `result`)
```json
{
  "success": true|false,
  "recipe": ParsedRecipe|null,
  "used_llm": boolean,
  "parser_strategy": "schema_org_json_ld" | "microdata" | "heuristic" | "llm_fallback",
  "warnings": ["string"],
  "error_code": "string|null",
  "error_message": "string|null"
}
```

### Job status
```json
{
  "id": "uuid",
  "status": "PENDING" | "RUNNING" | "COMPLETE" | "ERROR",
  "result": ParseResult|null,
  "error_code": "string|null",
  "error_message": "string|null"
}
```

## Client flow (recommended)
1. POST `/recipes/parse-url/async` with URL and `use_llm_fallback:true`.
2. Start polling `/recipes/parse-url/status/{job_id}` every 2–3s.
3. While `status` in {PENDING, RUNNING}: show loading.
4. On `COMPLETE`:
   - Use `result.recipe` to render preview.
   - Surface `warnings` (e.g., “LLM fallback used; please verify ingredients.”).
   - Optionally show `parser_strategy`.
   - Provide “Save” action that calls existing `POST /recipes` with mapped payload if desired.
5. On `ERROR`:
   - Display `error_code`/`error_message`.
   - Offer retry (re-enqueue) and/or manual edit.

## Error codes (common)
- `invalid_url` — URL format/blocked host
- `fetch_failed` — network/HTTP issues fetching the page
- `parse_failed` — all deterministic strategies failed and LLM disabled
- `llm_failed` — LLM call failed or invalid JSON
- `llm_timeout` — LLM call exceeded timeout
- `save_failed` — (sync path only) DB save failed

## Timeouts & retries (server)
- LLM call timeout ~90s in worker; retries up to `LLM_RECIPE_QUEUE_MAX_RETRIES` for transient errors (`llm_timeout`, `llm_failed`, `fetch_failed`).
- Queue worker polls every ~5s; jobs are processed in background.

## UX notes
- Expect some jobs to take ~20–90s when LLM fallback is needed; keep polling UI responsive.
- Show parser strategy and warnings for user trust.
- Keep URL input validated client-side; still rely on server validation.
- If no image is returned, allow user to attach one before saving.

## Env (for reference)
- `LLM_BASE_URL`, `LLM_RECIPE_MODEL`, `LLM_API_KEY` (server side)
- `LLM_RECIPE_QUEUE_MAX_RETRIES` controls background retries


