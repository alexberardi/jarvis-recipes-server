# Create Recipes via URL — Backend PRD

This PRD describes the **server-side implementation** for creating recipes from a **URL** in the `jarvis-recipes-server` FastAPI project.

The goal is to support this workflow:

1. Client sends a URL to the recipes server.
2. Server fetches and parses the page:
   - Try structured recipe data (schema.org JSON-LD / microdata) first.
   - Fallback to heuristic HTML extraction.
   - Optional final fallback: send extracted text/HTML to an LLM to produce a structured recipe.
3. Server returns a **normalized recipe payload** matching the existing recipe creation schema.
4. Client may then:
   - Display a preview
   - Optionally call the existing `POST /recipes` endpoint to persist the recipe.

> **Scope note:** This PRD is **backend-only**. It does not define mobile or web UI.

---

## 1. High-Level Design

### 1.1 Goals

- Provide a single backend endpoint that:
  - Accepts a recipe URL
  - Returns a **structured recipe** payload compatible with the current recipe creation API
- Prefer **deterministic scraping and schema.org parsing** over LLM when possible.
- Use an LLM only as a **fallback** when the site does not expose structured recipe data or when parsing fails.
- Make it easy to:
  - Add **domain-specific scrapers** later (e.g., `allrecipes.com`, `nytimes.com`)
  - Swap or configure the LLM backend via environment variables.
- Perform a fast, shallow validation pass before enqueueing async parsing jobs to avoid wasting work when the client abandons polling.

---

## 2. Endpoint Design

### 2.0 Shallow Preflight Validation
Before enqueueing any async parsing job, the route must perform a **cheap, synchronous preflight check** to validate that the request is worth processing.

The preflight check must:
- Validate the URL scheme and host (`http` / `https` only)
- Reject clearly unsupported content types (non-HTML responses)
- Attempt a **fast HEAD or lightweight GET** (with a short timeout, e.g. 2–3 seconds)
- Confirm that the response status is not an immediate hard failure (e.g. 404, 410)

The preflight check must **not**:
- Run full HTML parsing
- Invoke heuristics or LLMs
- Perform retries or proxy fallbacks

If the preflight check fails:
- The route returns an error response immediately
- No async job is created
- No worker resources are consumed

This protects the system from:
- Clients abandoning polling mid-job
- Obviously bad URLs
- Large volumes of low-quality or accidental requests

### 2.1 Endpoint: Parse Recipe from URL

**Method:** `POST`

**Path:** `/recipes/parse-url`

> Name can be adjusted to fit existing routing conventions (e.g. `/recipes/import/url`).

**Preflight requirement:**
This endpoint must run the shallow preflight validation (Section 2.0) before creating or enqueueing a parsing job. If preflight fails, the endpoint returns an error synchronously.

**Request Body (JSON):**

```json
{
  "url": "https://example.com/my-recipe",
  "use_llm_fallback": true,
  "save": false
}
```

- `url` (string, required): the URL to parse.
- `use_llm_fallback` (bool, optional, default `true`): whether to invoke the LLM fallback if deterministic parsing fails.
- `save` (bool, optional, default `false`): if `true`, the server will **also create** a recipe record and return its ID.

**Successful Response (JSON):**

```jsonc
{
  "success": true,
  "recipe": {
    "title": "Spaghetti Bolognese",
    "description": "A rich, slow-cooked meat sauce.",
    "source_url": "https://example.com/my-recipe",
    "image_url": "https://example.com/image.jpg",
    "tags": ["italian", "pasta", "dinner"],
    "servings": 4,
    "estimated_time_minutes": 60,
    "ingredients": [
      {
        "text": "spaghetti",
        "quantity_display": "12",
        "unit": "oz"
      },
      {
        "text": "ground beef",
        "quantity_display": "1",
        "unit": "lb"
      }
    ],
    "steps": [
      "Brown the beef.",
      "Add tomatoes and simmer.",
      "Cook the spaghetti.",
      "Combine and serve."
    ],
    "notes": [
      "You can substitute ground turkey.",
      "Freezes well for up to 3 months."
    ]
  },
  "created_recipe_id": null,
  "warnings": [
    "LLM fallback used; please verify ingredients."
  ],
  "used_llm": true,
  "parser_strategy": "llm_fallback"
}
```

- `recipe`: a Recipe DTO matching or easily mappable to the existing `POST /recipes` input schema.
- `created_recipe_id`: nullable; set only if `save=true` and creation succeeds.
- `warnings`: list of strings; may be empty.
- `used_llm` (bool): whether the LLM fallback was invoked and used.
- `parser_strategy` (string): e.g. `"schema_org_json_ld"`, `"microdata"`, `"heuristic"`, `"llm_fallback"`.

**Error Response (JSON):**

```json
{
  "success": false,
  "message": "Unable to parse recipe from URL.",
  "details": "Timeout while fetching the URL.",
  "error_code": "fetch_timeout"
}
```

Common error codes:
- `invalid_url`
- `fetch_failed`
- `unsupported_content_type`
- `parse_failed`
- `llm_failed`

---

## 3. Internal Architecture

### 3.0 Preflight Validator
Introduce a lightweight validator, e.g. `preflight_validate_url(url: str) -> PreflightResult`, responsible for determining whether a URL should be accepted for async parsing.

```py
class PreflightResult(BaseModel):
    ok: bool
    status_code: Optional[int]
    content_type: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
```

Responsibilities:
- Perform minimal network I/O (HEAD preferred; GET fallback if needed)
- Enforce strict timeouts
- Return clear failure reasons without throwing

Failure error codes may include:
- `invalid_url`
- `unsupported_content_type`
- `fetch_failed`
- `fetch_timeout`

### 3.1 Modules / Services

Introduce a new internal module, e.g. `app/services/url_recipe_parser.py`, containing:

- `fetch_html(url: str) -> str`
- `extract_recipe_from_schema_org(html: str, url: str) -> ParsedRecipe | None`
- `extract_recipe_heuristic(html: str, url: str) -> ParsedRecipe | None`
- `extract_recipe_via_llm(html: str, url: str, metadata: Optional[dict]) -> ParsedRecipe`
- `normalize_parsed_recipe(parsed: ParsedRecipe) -> RecipeCreateDTO`

and a main orchestration function:

```py
async def parse_recipe_from_url(url: str, use_llm_fallback: bool = True) -> ParseResult:
    ...
```

Where `ParseResult` encapsulates:

```py
class ParseResult(BaseModel):
    success: bool
    recipe: Optional[ParsedRecipe]
    used_llm: bool
    parser_strategy: Optional[str]
    warnings: List[str] = []
    error_code: Optional[str]
    error_message: Optional[str]
```

### 3.2 Libraries & Dependencies

Recommended Python libraries:

- **HTTP client**
  - `httpx` (async-friendly, fits FastAPI)
- **HTML parsing**
  - `beautifulsoup4` for general parsing
  - Optionally `lxml` as a fast parser backend
- **JSON parsing**
  - Standard library `json`

New dependencies to add in `pyproject.toml` / `requirements.txt`:

- `httpx`
- `beautifulsoup4`
- (optional) `lxml`

### 3.3 Parsing Priority Order

The parser should attempt these strategies in order:

1. **Schema.org JSON-LD Recipe**
   - Search for `<script type="application/ld+json">` tags.
   - Parse JSON; look for objects where `"@type"` is `"Recipe"` or includes `"Recipe"` in a list.
   - Map fields: `name`, `description`, `image`, `recipeIngredient`, `recipeInstructions`, `recipeYield`, `totalTime`, `keywords`.
   - If successful, return `ParsedRecipe` with `parser_strategy="schema_org_json_ld"`.

2. **Microdata / RDFa Recipe**
   - Scan DOM for elements with `itemtype` containing `"schema.org/Recipe"`.
   - Extract fields via `itemprop` attributes.
   - If successful, return `ParsedRecipe` with `parser_strategy="microdata"`.

3. **Heuristic HTML Extraction**
   - As a fallback when structured data is not available.
   - Heuristics may include:
     - Main content container detection via common patterns: `article`, `main`, `.recipe`, `.post`, etc.
     - Ingredient detection via bullet lists (`<ul>`, `<ol>`) that contain likely ingredient-like text (numbers, fractions, units, food words).
     - Instructions detection via paragraphs or list items following headers like "Directions", "Instructions", "Method".
   - Return `ParsedRecipe` with `parser_strategy="heuristic"` if confidence is decent.

4. **LLM Fallback** (optional, controlled by `use_llm_fallback`)
   - If all above fail or produce incomplete results, send a structured prompt to the LLM backend.
   - Provide the LLM with:
     - URL
     - Page title
     - Cleaned main text content (e.g., text from `article` or `<body>` with boilerplate removed as much as is reasonable).
   - Ask the LLM to return **strict JSON** in the expected `ParsedRecipe` schema.
   - Return `ParsedRecipe` with `parser_strategy="llm_fallback"` and `used_llm=True`.

If all strategies fail:
- Return `ParseResult(success=False, error_code="parse_failed", ...)`.

---

## 4. Parsed Recipe Model

Define an internal Pydantic model for the parser output, e.g. `ParsedRecipe`:

```py
class ParsedIngredient(BaseModel):
    text: str
    quantity_display: Optional[str] = None
    unit: Optional[str] = None

class ParsedRecipe(BaseModel):
    title: str
    description: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = []
    servings: Optional[int] = None
    estimated_time_minutes: Optional[int] = None
    ingredients: List[ParsedIngredient] = []
    steps: List[str] = []
    notes: List[str] = []
```

This should be easily mappable to the existing recipe create schema used by `POST /recipes`.

---

## 5. LLM Integration

### 5.1 LLM Backend Assumptions

Assume there is (or will be) a separate **LLM proxy/API** (e.g. `jarvis-llm-proxy`) that exposes a **generic chat/completions endpoint**, not a recipe-specific route.

For example, something like:
- `POST /v1/chat/completions` or equivalent

For this project, the actual LLM proxy route is:
- `POST /api/v1/chat`

and it expects a body compatible with:

```python
class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "jarvis-llm"
    temperature: Optional[float] = 0.7
    messages: List[Message]
    conversation_id: Optional[str] = None
```

The recipes server will construct a payload matching this shape.

Configuration should be provided via environment variables, e.g.:
- `LLM_BASE_URL` (e.g. `http://jarvis-llm-proxy:8000`)
- `LLM_RECIPE_MODEL` (name/ID of the model to use for recipe parsing, defaults to `"jarvis-llm"` if not set)
- `LLM_API_KEY` (optional, if the proxy requires auth)

### 5.2 LLM Request Payload & Prompt

The recipes server will call the generic chat endpoint with a payload similar to:

```jsonc
{
  "model": "${JARVIS_VISION_MODEL_NAME}",
  "temperature": 0.0,
  "messages": [
    {
      "role": "system",
      "content": "You are a recipe extraction engine. Given noisy HTML-derived text, you extract a single recipe and output ONLY strict JSON that matches the provided schema. Do not include markdown or explanations."
    },
    {
      "role": "user",
      "content": "...constructed instruction with URL, title, and cleaned text (see below)..."
    }
  ],
  "conversation_id": null
}
```

Use a low temperature (0.0–0.1) to maximize determinism and avoid malformed JSON.

The user message content should:
- Briefly restate the task.
- Include the URL and page title.
- Include the cleaned/truncated main page text.
- Include an explicit JSON schema description, for example:

> The JSON schema you must follow is:
> ```json
> {
>   "title": "string",
>   "description": "string or null",
>   "source_url": "string or null",
>   "image_url": "string or null",
>   "tags": ["string"],
>   "servings": "number or null",
>   "estimated_time_minutes": "number or null",
>   "ingredients": [
>     {
>       "text": "string",
>       "quantity_display": "string or null",
>       "unit": "string or null"
>     }
>   ],
>   "steps": ["string"],
>   "notes": ["string"]
> }
> ```
>
> You must output ONLY one JSON object of that form.

The LLM proxy is free to map this into whatever internal API (OpenAI-compatible, Ollama, etc.), but from the recipes server perspective it is just a generic chat completion call.

### 5.3 LLM Response Handling

On the recipes server side, after receiving the chat completion response:

- Extract the `content` string from the assistant message.
- Parse it as JSON into `ParsedRecipe`.
- Set `used_llm=True` and `parser_strategy="llm_fallback"`.
- Add a warning such as "LLM fallback used; please verify ingredients." to the response.

If the LLM call fails (timeout, 5xx, invalid JSON):
- Return `success=false` with `error_code="llm_failed"` and an appropriate message.

---

## 6. Security, Performance & Limits

- **Timeouts**
  - Set sane timeouts on `fetch_html` (e.g. 5–10 seconds).
  - Set a timeout on the LLM call (e.g. 15–20 seconds).

- **Content size limits**
  - Avoid sending the entire HTML document to the LLM.
  - Extract and truncate main content text to a max length (e.g. 10–20k characters).

- **URL validation**
  - Ensure `url` is a valid HTTP/HTTPS URL.
  - Optionally block obviously local or internal hosts (e.g. `localhost`, private IP ranges) for safety.

- **Rate limiting**
  - Out of scope for this PRD, but we should design the code so that rate limiting can be added at the router or middleware layer later.

---

## 7. Integration with Existing Recipe Creation

If `save=false` (default):
- The endpoint **does not touch the database**.
- It only returns the parsed `recipe` payload.

If `save=true`:
- After a successful parse:
  - Convert `ParsedRecipe` → existing `RecipeCreate` schema.
  - Call the same internal service used by the `POST /recipes` endpoint to create a record.
  - Return the created recipe ID as `created_recipe_id`.

If recipe creation fails (e.g. DB error):
- Return `success=false` with appropriate `error_code` and message.

---

## 8. Testing & Validation

### 8.1 Unit Tests

- **HTML Parsing**
  - Test `extract_recipe_from_schema_org` with sample JSON-LD payloads (single and multiple `@type` entries).
  - Test `extract_recipe_heuristic` with mocked HTML snippets containing realistic ingredients and instructions.

- **LLM integration**
  - Mock the LLM HTTP client to return a valid `ParsedRecipe` JSON.
  - Test error handling when LLM returns invalid JSON or times out.

- **parse_recipe_from_url Orchestration**
  - Test flows:
    - Schema.org success → no LLM used.
    - Schema.org fail → heuristic success.
    - Schema + heuristic fail → LLM success.
    - All strategies fail → `success=false`.

### 8.2 Integration Tests (optional / nice-to-have)

- End-to-end test for `/recipes/parse-url` using a couple of real or fixture HTML pages stored locally
  (served via a test HTTP server or as local file content fed into the parser).

---

## 9. Implementation Steps

0. Implement shallow preflight validation and wire it into the route before job enqueueing.

1. Add dependencies (`httpx`, `beautifulsoup4`, optionally `lxml`).
2. Implement `url_recipe_parser.py` service module with:
   - `fetch_html`
   - `extract_recipe_from_schema_org`
   - `extract_recipe_heuristic`
   - `extract_recipe_via_llm`
   - `parse_recipe_from_url`
3. Define `ParsedIngredient`, `ParsedRecipe`, and `ParseResult` Pydantic models.
4. Implement the `POST /recipes/parse-url` endpoint in the router:
   - Validate input.
   - Call `parse_recipe_from_url`.
   - Optionally create a recipe when `save=true`.
5. Wire environment variables for LLM integration.
6. Add unit tests for parsing and orchestration.
7. (Optional) Add integration test using test HTML fixtures.

This PRD should be used as the source of truth for implementing the URL-based recipe creation in the `jarvis-recipes-server` project.
