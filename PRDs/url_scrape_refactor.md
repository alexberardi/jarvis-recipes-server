# PRD: WebView-Assisted URL Recipe Import (Server)

## Summary
Some recipe sites (notably Food Network and other WAF/anti-bot protected domains) intermittently return **403/blocked** to server-side HTML fetches, even with browser-like headers and proxy fallback. We will add a **client-assisted fallback ingestion path** that allows the mobile client to load the page in a WebView and extract structured data (prefer JSON-LD) and submit it back to the server for deterministic parsing.

This PRD covers **server-side** changes only. A separate PRD will cover the **React Native** client/WebView UX and extraction behavior.

## Goals
- Preserve the existing **async job** contract for URL parsing.
- When server fetch is blocked, return a **clear, structured signal** that the client can use to initiate a WebView extraction fallback.
- Add a new async ingestion endpoint that accepts **client-extracted payload** (JSON-LD preferred, optional HTML snippet) and returns a `recipe_draft` using the same normalization rules.
- Keep tests deterministic by validating server logic against stored fixtures (JSON-LD/HTML snippets), not live sites.
- Simplify the parsing pipeline by removing legacy URL-only parsing paths; backwards compatibility with the previous implementation is **not required**.

## Non-Goals
- Building a backend headless Chromium renderer in this iteration.
- Guaranteeing server-side fetching will work for all sites.
- Bypassing paywalls or restricted content.
- Preserving the legacy URL parsing worker implementation or maintaining dual pipelines.

## Current Contract (Baseline)
The existing URL-only async parsing contract is documented here for reference only. This refactor intentionally **does not preserve backwards compatibility** with the legacy implementation unless explicitly justified below.

The legacy flow may be removed or substantially rewritten as part of this change.

### Start job
- `POST /recipes/parse-url/async`
  - body: `{ "url": "https://example.com/recipe" }`
  - auth: Bearer JWT
  - response: `{ "id": "<job_id>", "status": "PENDING" }`

### Poll job
- `GET /recipes/jobs/{job_id}`
  - success: `status=COMPLETE`, `result={ recipe_draft, pipeline }`
  - error: `status=ERROR`, `result=null`, `error_code`, `error_message` set

### Worker pipeline
- fetch HTML (headers + optional `SCRAPER_COOKIES` + proxy fallback `https://r.jina.ai/<url>`)
- parse strategy order: json-ld → microdata → heuristic → llm_fallback
- normalize (including `notes: null` → `[]`) then validate into `ParsedRecipe` → `RecipeDraft`

## Proposed Design

## Unified Ingestion & Worker Interface
All recipe imports—URL, client WebView payload, or image-based—flow through a **single server-side worker interface**. Source-specific concerns are resolved *before* parsing.

### IngestionInput (internal)
```python
class IngestionInput:
    source_url: str | None
    source_type: Literal[
        "server_fetch",
        "client_webview",
        "image_upload"
    ]

    # Optional content (one or more may be present)
    raw_html: str | None
    jsonld_blocks: list[str] | None
    images: list[ImageRef] | None  # image bytes or object-store refs

    # Metadata
    client_context: dict | None
```

```python
class ImageRef:
    content_type: str           # image/jpeg, image/png
    bytes: bytes | None         # direct upload (size-limited)
    object_url: str | None      # presigned URL or internal object ref
```

### Worker Entry Point
```python
def parse_recipe(input: IngestionInput) -> ParseResult:
    ...
```

The worker:
- Selects parsing strategies based on **available content**, not source
- Applies identical normalization and validation rules for all sources
- Uses LLMs only as a last resort

### 1) Extend Job Result to Indicate Client Fallback
When fetch fails due to blocking, the server should return a structured response indicating the required client action. Because backwards compatibility is not required, the server may freely change error semantics, response shapes, and job states to better support the WebView-assisted flow.

#### New optional fields (top-level job response)
- `next_action` *(string|null)*: suggested follow-up for the client.
  - values:
    - `"webview_extract"` — open WebView and extract JSON-LD/HTML
    - `null` — no suggestion
- `next_action_reason` *(string|null)*: short machine-readable reason.
  - values:
    - `"blocked_by_site"`
    - `"consent_required"` (future)
    - `"unsupported_site"` (future)

#### Rules
- Set `next_action="webview_extract"` when:
  - `error_code == "fetch_failed"` AND
  - pipeline warnings contain `"blocked_by_site"` OR HTTP status is 401/403 from the origin.

> Backwards compatibility: existing clients can ignore `next_action`.

#### Example (blocked)
```json
{
  "id": "abc",
  "status": "ERROR",
  "result": null,
  "error_code": "fetch_failed",
  "error_message": "status_403",
  "next_action": "webview_extract",
  "next_action_reason": "blocked_by_site"
}
```

### 2) Add Client-Extracted Payload Ingestion (Async)
Introduce a new async endpoint that mirrors the existing job flow but uses **client-provided extraction** rather than server fetch.

#### Endpoint
- `POST /recipes/parse-payload/async`

#### Request body
```json
{
  "source": {
    "type": "url",
    "source_url": "https://www.foodnetwork.com/..."
  },
  "extraction": {
    "jsonld": ["<raw ld+json string>", "..."],
    "html_snippet": "<optional trimmed html>",
    "extracted_at": "2025-12-15T01:23:45Z",
    "client": {
      "platform": "ios|android",
      "app_version": "x.y.z"
    }
  }
}
```

##### Notes
- `jsonld` is preferred and should be supported as an array (some pages contain multiple JSON-LD blocks).
- `html_snippet` is optional and should be **size-limited** (see Security/Abuse).

#### Response
Same job envelope:
```json
{ "id": "<job_id>", "status": "PENDING" }
```

#### Job completion result
Same `result` shape:
- `result.recipe_draft` populated on success
- `result.pipeline` includes a new `parser_strategy` value (see below)

### 3) Worker Pipeline Changes
Add a second entrypoint in the worker:
- `parse_recipe_from_url(url)` (existing)
- `parse_recipe_from_payload(payload)` (new)

#### Parser strategies
Extend `pipeline.parser_strategy` enum with:
- `"client_json_ld"` — parsed from client-provided JSON-LD
- `"client_html"` — parsed from client-provided HTML snippet (heuristic/microdata)
- `"client_llm_fallback"` — LLM used on client-provided content

#### Strategy order for payload jobs
1) **client_json_ld**: parse schema.org Recipe from JSON-LD blocks
2) **microdata/heuristic** against `html_snippet` if present
3) **LLM fallback** using the extracted content (jsonld + html_snippet) if still incomplete

#### Normalization rules (must match URL flow)
- `notes`: null/missing → `[]`
- ingredients/steps cleanup identical
- enforce max lengths (see Security/Abuse)

### 3a) Image-Based Parsing Workflow
The unified worker also supports **image-first recipe ingestion** (e.g., photos of recipe cards, cookbooks, screenshots).

#### Image ingestion
Images may enter the system via:
- Mobile camera capture
- Image picker / gallery
- Screenshot import

The API layer constructs `IngestionInput` with:
- `source_type = "image_upload"`
- `images = [ImageRef, ...]`

#### Strategy order for image jobs
1) **Vision OCR / VLM extraction**
   - Extract text blocks (ingredients, steps, metadata)
   - Optionally detect structure (lists, headings)

2) **Heuristic structuring**
   - Map OCR output into draft ingredients/steps
   - Attempt time/servings inference

3) **LLM fallback (image-aware)**
   - Provide OCR text + images (or image embeddings) to the LLM
   - Request strict JSON recipe output

4) **Normalization & validation**
   - Same rules as URL/WebView imports
   - `notes`: null → `[]`

#### Parser strategy values (extended)
Extend `pipeline.parser_strategy` enum with:
- `"image_ocr"`
- `"image_llm_fallback"`

#### Notes
- Image parsing never attempts server-side HTML fetch.
- Image jobs may consist of multiple images; order should be preserved.

### 4) Domain Policy (When to Suggest WebView)
We should avoid forcing WebView for everything. The suggestion should be driven by evidence:

#### Default behavior
- Only suggest `next_action=webview_extract` when blocked is detected.

#### Optional allowlist override (config)
- `WEBVIEW_FALLBACK_DOMAINS` env var (comma-separated)
  - if the domain is on the list and fetch fails, always suggest fallback.
  - initial candidates: `www.foodnetwork.com`

### 5) API Error Codes & Messages
No changes to existing codes are required.

- Keep `error_code="fetch_failed"` for server fetch failures.
- Keep `warnings=["blocked_by_site", ...]` inside `pipeline` where applicable.
- Add `next_action` and `next_action_reason` as described.

### 6) Observability
Add structured logs and metrics:
- job type: `url` vs `payload`
- domain
- parser strategy chosen
- blocked rate per domain
- payload sizes (jsonld bytes, html_snippet bytes)

## Security, Abuse, and Limits
Client payload ingestion is user-controlled input; apply strict limits:
- Max `jsonld` blocks: 10
- Max size per JSON-LD block: 200 KB
- Max `html_snippet` size: 400 KB (configurable)
- Reject payloads larger than limits with `error_code="invalid_payload"` (new) OR reuse existing validation code if present.
- Sanitize logs (do not log full HTML; log hashes/byte sizes).
- Only allow authenticated users (same JWT auth).

## Backwards Compatibility
Backwards compatibility with the legacy URL-only parsing flow is **not required**.

The server may:
- Remove or refactor `POST /recipes/parse-url/async`
- Change job response shapes and error semantics
- Consolidate URL-based and payload-based parsing into a single ingestion pipeline

Any retained legacy code paths must be explicitly justified (e.g., internal tools, migration window) and documented.

## Testing Strategy
### Unit tests
- JSON-LD parsing:
  - Recipe object single block
  - multiple blocks with one Recipe
  - graph form (`@graph`) recipes
  - malformed JSON-LD (should fail gracefully)

### Integration tests (deterministic, no live web)
Add fixtures under a new test folder (example path):
- `tests/fixtures/jsonld/foodnetwork_baked_beans.jsonld`
- `tests/fixtures/html/foodnetwork_baked_beans_snippet.html`

Test cases:
1) payload with JSON-LD only → `client_json_ld`
2) payload with html_snippet only → `client_html`
3) payload incomplete → `client_llm_fallback`
4) payload too large → `invalid_payload`

### Live site smoke (non-gating)
Optional nightly job:
- attempt server fetch on known-problem URLs
- record status + block rate

## Rollout Plan
1) Remove or disable the legacy URL-only parsing worker and replace it with the new unified ingestion pipeline (URL fetch + payload ingestion).
2) Deploy and verify:
   - blocked jobs now return structured hints (no crashes)
   - payload endpoint works with fixtures
3) Implement client PRD and release RN app update.

## Open Questions / Future Enhancements
- Do we want to store payload artifacts for debugging (encrypted, time-limited)?
- Should we add a synchronous variant for payload parsing (likely no; keep async for uniformity)?
- Add `consent_required` detection once we see common patterns.

---

# PRD: WebView-Assisted URL Recipe Import (Client)

> Client PRD will be authored separately. At a high level it will cover:
- UX on `fetch_failed + blocked_by_site`: show “Browser mode import” CTA
- WebView load + optional consent interaction
- JS extraction:
  - extract `script[type="application/ld+json"]`
  - optional trimmed HTML snippet
- Submit to `POST /recipes/parse-payload/async` and poll jobs
- Offline/deterministic tests using local fixture pages
