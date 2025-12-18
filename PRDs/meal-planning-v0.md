

# Meal Planning v0 (Generate Only, Stage Recipes)

## Summary
Meal Planning v0 generates a proposed meal plan for a user-specified date range and meal slots. It does **not** persist meal plans yet, but it **may** create **staged recipes** for any suggestions that are not already in the user’s cookbook so the client can drill into details before confirmation.

The output is produced asynchronously via a job + mailbox flow.

---

## Terminology

### MealType
```ts
type MealType = "breakfast" | "lunch" | "dinner" | "snack" | "dessert";
```

### Repeat
`repeat` is an optional hint that the user intends to reuse the same (or similar) meal for meal-prep.

- `mode: "same"` means: reuse the **same** selected recipe for multiple future slots.
- `mode: "similar"` means: pick different recipes that fit the same constraints.
- `count` is how many total occurrences the user wants (including the current slot).

Example: `{ "mode": "same", "count": 3 }` means “use the same recipe for this meal and 2 additional slots (3 total).”

(Actual algorithm details will be specified later; for v0 we preserve this field and can apply it in a simple way.)

---

## Server API Contracts

### Endpoint
`POST /meal-plans/generate/jobs`

- Creates a job to generate a proposed plan.
- Returns immediately with a job id + request id.
- Result is delivered via mailbox.

### Request JSON Schema
```json
{
  "days": [
    {
      "date": "YYYY-MM-DD",
      "meals": {
        "breakfast": {
          "servings": 3,
          "tags": ["easy", "healthy"],
          "note": "string",
          "is_meal_prep": false,
          "repeat": { "mode": "same|similar", "count": 2 }
        },
        "lunch": { "servings": 1 },
        "dinner": { "servings": 4 },
        "snack": { "servings": 1 },
        "dessert": { "servings": 4 }
      }
    }
  ],

  "preferences": {
    "hard": {
      "allergens": ["string"],
      "excluded_ingredients": ["string"],
      "diet": "string|null"
    },
    "soft": {
      "tags": ["string"],
      "cuisines": ["string"],
      "max_prep_minutes": 35,
      "max_cook_minutes": 60
    }
  },

  "allow_external_recipes": false
}
```

#### Notes
- `days[].meals` is a map of optional meal keys. If a meal is omitted for a day, it is skipped.
- `tags` are lightweight labels like `"easy"`, `"healthy"`, `"fancy"`, etc.
- `note` is free-text like `"chicken"` / `"no pasta"` / `"spicy"`.
- `allow_external_recipes` controls whether generation can use the “core registry” (server-side) in addition to the user’s cookbook.

### Immediate Response (202)
```json
{
  "job_id": "uuid",
  "request_id": "uuid"
}
```

---

## Mailbox Contracts

### Completion Message
Type: `meal_plan_generation_completed`

Payload:
```json
{
  "request_id": "uuid",
  "result": {
    "days": [
      {
        "date": "YYYY-MM-DD",
        "meals": {
          "dinner": {
            "servings": 6,
            "tags": ["fancy", "easy"],
            "note": "chicken",
            "is_meal_prep": false,
            "repeat": null,
            "selection": {
              "source": "user|core|stage",
              "recipe_id": "uuid",
              "confidence": 0.82,
              "matched_tags": ["easy"],
              "warnings": []
            }
          }
        }
      }
    ]
  }
}
```

### Failure Message
Type: `meal_plan_generation_failed`

Payload:
```json
{
  "request_id": "uuid",
  "error_code": "string",
  "message": "string"
}
```

### Slot Failure Behavior (per-meal)
If no matching recipe can be selected for a meal slot, return the slot with:

```json
"selection": null
```

Client should display:
> “Could not find a recipe fitting your criteria. Try loosening your constraints or adding recipes that fit the selections.”

---

## Recipe Drill-In (Client Navigation)

The response includes `selection.source` and `selection.recipe_id` to support drill-in.

Routing assumption:
- Client can fetch recipe details by source + id, e.g.:
  - `GET /recipes/user/{id}`
  - `GET /recipes/core/{id}`
  - `GET /recipes/stage/{id}`

(Exact route naming can be adjusted later, but the `source` field is included to enable this pattern.)

---

## Stage Recipes (Server-side)

### Purpose
When the generator selects a recipe that is not yet in the user’s cookbook (e.g., from `core`), the server creates a **stage recipe** record so:
- The client can fetch full details for drill-in.
- The user can confirm or swap meals before committing.

### Lifecycle
- If the user **confirms** a generated plan (future feature):
  - Copy staged recipes into the real cookbook recipes table.
  - Delete stage recipes that were copied.
- If the user **swaps** a meal suggestion (future feature):
  - Delete any stage recipe that is no longer referenced.
- Cleanup job (future):
  - Delete stage recipes not referenced/confirmed after **3 days**.

### Stage Recipe Data Shape (minimal)
Times are stored as minutes, split into prep and cook.

```json
{
  "id": "uuid",
  "title": "string",
  "description": "string|null",
  "yield": "string|null",
  "prep_time_minutes": 0,
  "cook_time_minutes": 0,
  "ingredients": [
    { "text": "string", "section": "string|null" }
  ],
  "steps": [
    { "text": "string", "section": "string|null" }
  ],
  "tags": ["string"],
  "notes": ["string"],
  "created_at": "ISO-8601",
  "expires_at": "ISO-8601"
}
```

No household fields in v0.

---

## Client Contracts (Shared Spec)

### Client Request Model (RN + Web)
Client should construct the request using:
- A `days[]` array
- Each day has a `meals` object with optional keys
- Each meal slot has at minimum `servings`

### Client Render Model
Client can render the returned plan grid using:
- `days[].date`
- `days[].meals` keys present
- For each slot:
  - show servings
  - show tags/note
  - if `selection != null`, render selected title after fetching details from `/recipes/{source}/{id}`
  - if `selection == null`, show the standard “no match” message

### Estimated Total Time
Client should display:
- `prep_time_minutes`
- `cook_time_minutes`
- total estimated time = `prep_time_minutes + cook_time_minutes`

---

## Out of Scope for v0
- Persisting the meal plan itself (save/confirm workflow)
- Household sharing
- Shopping list generation
- Algorithm details (ranking, variety rules, constraint relaxation)

---

## Next Specs
1) Tool specs for generation (search + get recipe + stage recipe helpers)
2) Generation orchestration + prompt contracts
3) Client UX: generation flow, progress, swap/refresh actions