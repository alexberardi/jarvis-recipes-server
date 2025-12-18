# Ingredients & Units PRD — Jarvis Recipes (Server)

This document defines the changes needed in **jarvis-recipes-server** to support a richer ingredient model and better UX for the **Create Recipe** flow.

The key goals are:
- Extend ingredients to optionally include **structured quantity and unit of measure (UoM)** while preserving a free-form representation.
- Introduce **stock ingredients** and **stock units of measure** to power autocomplete/search.
- Provide a simple, admin-only way to **seed / upsert** stock data from static JSON files.

A separate PRD will cover the mobile/client implementation. This one is **server-focused**.

---

## 1. Data Model Changes

### 1.1 Existing Ingredient Model (per recipe)

Current shape (conceptual):

- Table: `recipe_ingredients`
- Fields (simplified):
  - `id` (PK)
  - `recipe_id` (FK to recipes)
  - `text` (string) — e.g. `"1 cup flour"`

### 1.2 New Ingredient Fields

We want to keep the free-form `text` field but enrich each ingredient with optional structured data.

**New fields on `recipe_ingredients`:**
- `quantity_display` — string (nullable)
  - The exact quantity text the user enters: `"1/2"`, `"1 1/2"`, `"½"`, `"3/4"`, `"2 big handfuls"`, etc.
  - Preserves full fidelity of recipe input for display and LLM context.
- `quantity_value` — numeric/Decimal (nullable)
  - Parsed numerical value when possible, e.g. `0.5`, `1.5`, `2`.
  - Used for math operations (shopping list totals, scaling recipes, etc.).
- `unit` — string (nullable)
  - Human-friendly UoM to display, e.g. `"tbsp"`, `"tablespoon"`, `"cup"`, `"g"`, `"oz"`.

**Guidelines:**
- `text` remains **required** and is the primary source of truth for display/search.
- `quantity_display`, `quantity_value`, and `unit` are **optional**; they enhance, not replace, `text`.
- Existing recipes without quantity/unit remain valid.

### 1.3 Quantity Parsing Rules (Backend Only)

The server will attempt to convert `quantity_display` into `quantity_value` using a limited, predictable set of formats.

Supported formats:
- Integer: `"1"` → `1`
- Decimal: `"0.5"` → `0.5`
- Simple fraction: `"1/2"` → `0.5`
- Mixed number: `"1 1/2"` → `1.5`

Rules:
- Strip whitespace before parsing.
- If parsing fails or denominator is zero, `quantity_value` remains `NULL`.
- **No** arbitrary expression evaluation (e.g. `"1+1/2"`, `"2 * 1/3"`) is supported.

The parsing logic should live in a reusable helper, e.g. `parse_quantity_display(raw: str) -> Decimal | None`, and should be unit-tested.

### 1.4 Pydantic Schemas

All server-side ingredient schemas used in recipe create/read/update flows should be updated.

Base shape:

```python
class IngredientBase(BaseModel):
    text: str
    quantity_display: str | None = None
    quantity_value: Decimal | None = None
    unit: str | None = None
```

For **incoming requests**:
- Clients will send `text` and may send `quantity_display` and `unit`.
- The server should compute `quantity_value` from `quantity_display`.
- Clients should **not** be required to send `quantity_value`.

For **responses**:
- The server should return all four fields:

```json
{
  "text": "cumin",
  "quantity_display": "1 1/2",
  "quantity_value": 1.5,
  "unit": "tbsp"
}
```

---

## 2. Stock Tables for Autocomplete

We will add two new tables that are used **only** for autocomplete suggestions in the UI.

> These tables are NOT foreign-keyed into `recipe_ingredients`.
> They are lookup tables used by search endpoints and the client.

### 2.1 Stock Ingredients

**Table:** `stock_ingredients`

Fields:
- `id` (PK)
- `name` (string, unique-ish)
  - e.g. `"chicken breast"`, `"chicken thigh"`, `"tortilla chips"`, `"cumin"`
- `created_at` (timestamp)
- `updated_at` (timestamp)

**Usage:**
- Drives autocomplete suggestions for ingredient names when the user types in the ingredient text/name field.
- Future enhancements may add categories, tags, etc., but not required for MVP.

**Search behavior:**
- Case-insensitive `ILIKE` match on `name`.
- Partial match: user typing `"chi"` should match `"chicken breast"`, `"chicken thigh"`, `"tortilla chips"`.

### 2.2 Stock Units of Measure

**Table:** `stock_units_of_measure`

Fields:
- `id` (PK)
- `name` (string, unique)
  - Full name, e.g. `"tablespoon"`, `"teaspoon"`, `"cup"`, `"pint"`, `"gram"`, `"ounce"`.
- `abbreviation` (string, nullable but recommended unique)
  - e.g. `"tbsp"`, `"tsp"`, `"c"`, `"pt"`, `"g"`, `"oz"`.
- `created_at` (timestamp)
- `updated_at` (timestamp)

**Search behavior:**
- Case-insensitive match over both `name` and `abbreviation`.
- Partial match allowed:
  - user types `"tbs"` → match `"tablespoon"` via abbreviation `"tbsp"`.
  - user types `"tea"` → match `"teaspoon"` via full name.

**Usage in recipes:**
- When the client selects a UoM from autocomplete, it writes the chosen display value into `recipe_ingredients.unit` via the API.
- No FK is needed; if stock UoM names change later, existing recipes are unaffected.

---

## 3. API Changes (Server)

### 3.1 Ingredient Autocomplete Endpoints

These endpoints live in `jarvis-recipes-server` and are consumed by the client.

#### GET `/ingredients/stock`

Query params:
- `q` (string, optional, default empty) — search term
- `limit` (int, optional, default 10)

Behavior:
- If `q` is empty → return top N common/pinned ingredients (MVP: any N rows).
- If `q` is non-empty → `ILIKE` search on `name` using `%q%`.

Response (array):

```json
[
  { "id": 1, "name": "chicken breast" },
  { "id": 2, "name": "chicken thigh" }
]
```

#### GET `/units/stock`

Query params:
- `q` (string, optional, default empty)
- `limit` (int, optional, default 10)

Behavior:
- If `q` is empty → return a default set of common UoMs.
- If `q` is non-empty → case-insensitive search over both `name` and `abbreviation`.

Response (array):

```json
[
  { "id": 1, "name": "tablespoon", "abbreviation": "tbsp" },
  { "id": 2, "name": "teaspoon", "abbreviation": "tsp" }
]
```

Authentication:
- These endpoints should require the standard JWT auth dependency, but results are not user-specific.

### 3.2 Ingredient Representation in Recipe Endpoints

All recipe-related endpoints that read/write ingredients should now use the extended ingredient shape.

Example JSON for an ingredient in a recipe response:

```json
{
  "text": "cumin",
  "quantity_display": "1 1/2",
  "quantity_value": 1.5,
  "unit": "tbsp"
}
```

Migration strategy:
- On the server, treat omitted `quantity_display` and `unit` as `null`.
- `text` remains required in all create/update operations.
- For existing clients (if any), payloads like `{ "text": "1 cup flour" }` remain valid; the server accepts payloads without `quantity_display`/`unit` and leaves `quantity_value` as `null`.
- New clients can send structured fields where available.

### 3.3 Request Payload Expectations

For a recipe create/update request, the client will send ingredients like:

```json
{
  "text": "cumin",
  "quantity_display": "1/2",
  "unit": "tbsp"
}
```

The server will:
- Store `text` and `quantity_display` as given.
- Compute `quantity_value` via the parsing helper.
- Store `unit` as a plain string.

The server should **not** require clients to send `quantity_value`.

---

## 4. Static Data & Seeding

We want a simple, repeatable way to pre-populate stock ingredients & UoMs from JSON files.

### 4.1 Static Data Files

Add a top-level folder to `jarvis-recipes-server`, for example:

- `static_data/ingredients.json`
- `static_data/units_of_measure.json`

**Example `ingredients.json`:**

```json
[
  { "name": "chicken breast" },
  { "name": "chicken thigh" },
  { "name": "tortilla chips" },
  { "name": "cumin" },
  { "name": "garlic" }
]
```

**Example `units_of_measure.json`:**

```json
[
  { "name": "tablespoon", "abbreviation": "tbsp" },
  { "name": "teaspoon", "abbreviation": "tsp" },
  { "name": "cup", "abbreviation": "c" },
  { "name": "pint", "abbreviation": "pt" },
  { "name": "gram", "abbreviation": "g" },
  { "name": "ounce", "abbreviation": "oz" }
]
```

Server logic will:
- Open these files from disk.
- Loop through entries.
- **Upsert** rows based on unique keys:
  - Ingredients: unique by `name`.
  - UoMs: unique by `name` or `abbreviation` (case-insensitive where reasonable).

### 4.2 Admin Seeding Endpoint

We need a simple way to trigger seeding/upserting via an HTTP endpoint, but it must be restricted.

**Endpoint:** `POST /admin/static-data/seed`

Behavior:
- Reads `static_data/ingredients.json` and `static_data/units_of_measure.json`.
- Upserts into `stock_ingredients` and `stock_units_of_measure`.
- Returns a summary, for example:

```json
{
  "ingredients_inserted": 10,
  "ingredients_updated": 2,
  "units_inserted": 6,
  "units_updated": 0
}
```

**Authentication / Authorization:**

For MVP, we can use a simple **admin secret header**, with the understanding that in a full Jarvis multi-service world this might be replaced by a proper user/role system.

- Env var: `STATIC_ADMIN_SECRET` (long random string)
- Header name: `X-Admin-Secret`
- Logic:
  - If header is missing or does not match the configured secret, return `403 Forbidden`.

Notes:
- This is acceptable if the endpoint is only reachable on trusted networks (e.g. localhost, tunnel during development).
- Future enhancement: replace with a proper admin-only JWT or `jarvis-auth` role-based access.

---

## 5. Security & Performance Considerations

- Admin seeding endpoint should not be exposed publicly without additional security.
- Autocomplete endpoints should have:
  - A sensible `limit` (e.g. max 50).
  - Basic indexes on `name` and `abbreviation` for efficient search.
- Future: rate limiting on autocomplete if exposed to the internet.

---

## 6. Implementation Checklist (Server)

**Data model & schemas**
- [ ] Add `quantity_display` (string, nullable), `quantity_value` (numeric/Decimal, nullable), and `unit` (string, nullable) to `recipe_ingredients` table and ORM model.
- [ ] Extend ingredient Pydantic schemas (`IngredientCreate`, `IngredientRead`, etc.) with `quantity_display`, `quantity_value`, and `unit`.
- [ ] Implement a backend helper `parse_quantity_display` that follows the rules in section 1.3.
- [ ] Ensure existing tests still pass, and that legacy `{ text }`-only payloads remain valid.

**Stock tables & autocomplete**
- [ ] Create `stock_ingredients` and `stock_units_of_measure` tables and ORM models.
- [ ] Implement `GET /ingredients/stock` with search behavior described in section 3.1.
- [ ] Implement `GET /units/stock` with search behavior described in section 3.1.

**Static data & seeding**
- [ ] Add `static_data/ingredients.json` and `static_data/units_of_measure.json` with an initial seeded set.
- [ ] Implement `POST /admin/static-data/seed` endpoint with `X-Admin-Secret` header guard.
- [ ] Make seeding idempotent (re-running should update existing rows, not duplicate them).

**Testing**
- [ ] Add unit tests for `parse_quantity_display` (valid & invalid cases).
- [ ] Add API tests for recipe creation with fractional quantities to verify `quantity_display` and `quantity_value` behavior.
- [ ] Add tests for `GET /ingredients/stock` and `GET /units/stock` search behavior.
- [ ] Add tests for the seeding endpoint, including auth failure and idempotent behavior.

---

This PRD should be used by the codegen assistant in Cursor to implement the new ingredient and unit system in **jarvis-recipes-server**. A separate PRD will cover the mobile/client behavior and UI.
