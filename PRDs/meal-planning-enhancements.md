# Meal Planning – LLM Enhancements PRD

## Overview
This document specifies enhancements to the meal-planning generation pipeline to introduce an LLM-driven reasoning layer. The goal is to improve intent interpretation, recipe selection quality, variety across days, and overall user satisfaction, while keeping deterministic server control and avoiding hallucinated recipes.

The LLM will **not** fetch or create recipes. It will act purely as a reasoning and ranking layer over server-provided candidates.

---

## Goals

- Use an LLM to interpret user intent (notes, tags, preferences)
- Improve recipe selection quality beyond deterministic filtering
- Maintain variety across days using recent meal history
- Allow partial failures without failing the entire plan
- Preserve testability, determinism, and server authority

---

## Non-Goals

- Persisting finalized meal plans (v0)
- Allowing the LLM to invent recipes
- Letting the LLM directly access databases or external APIs
- Full household-aware planning (future work)

---

## High-Level Architecture

For each meal slot (date + meal_type):

1. **Deterministic Candidate Gathering (Server)**
   - Server calls `search_recipes(...)` with:
     - meal_type
     - tags
     - preferences
   - Searches both user recipes AND stock/core recipes (always)
   - Limit candidates to a max of 25

2. **LLM Reranking & Intent Interpretation**
   - LLM receives:
     - slot configuration
     - global preferences
     - recent meal history
     - candidate summaries
   - LLM selects the best recipe OR explicitly returns `null`

3. **Server Enforcement**
   - Server validates the LLM response
   - Stages the selected recipe if required
   - Publishes mailbox progress + completion events
   - Server must write the selected recipe into the slot as `selection={source, recipe_id}` (or `selection=null` on failure)
   - Server must include `slot_failures_count` in the final job result

---

## LLM Responsibilities

The LLM is responsible for:

- Interpreting free-text notes ("I want chicken", "something quick")
- Softening overly strict constraints when appropriate
- Ranking candidates based on overall fit
- Maintaining variety across days
- Returning `null` when no candidate reasonably fits

The LLM is **not** allowed to:

- Create new recipes
- Modify recipe data
- Select recipes not provided in the candidate list

---

## Previous Meal History (Variety Control)

### Purpose
To avoid repetition and improve perceived intelligence, the LLM should consider recent meals when selecting recipes.

### Input to LLM
The server will provide a `recent_meals` block, containing up to the last N meals (default: 7 days):

```json
{
  "recent_meals": [
    {
      "date": "2025-12-14",
      "meal_type": "dinner",
      "recipe_id": "core_123",
      "title": "Grilled Chicken Bowl",
      "tags": ["chicken", "healthy"]
    }
  ]
}
```

### Guidance to LLM

- Prefer variety across consecutive days
- Avoid repeating the same recipe within the recent window
- Avoid repeating the same *primary protein* back-to-back when alternatives exist
- Variety is a **soft constraint**, not a hard rule

---

## User-Pinned Recipes (Future)

### Concept
The user may explicitly select a recipe they want for a given meal slot ("I definitely want this one").

### Behavior

- If a `pinned_recipe_id` is provided for a slot:
  - The server will skip candidate search
  - The LLM will not be invoked
  - The recipe will be staged directly

This is intentionally deferred until after the LLM integration is complete, but the architecture should not block it.

---

## LLM Input Schema (Per Slot)

```json
{
  "slot": {
    "date": "2025-12-17",
    "meal_type": "lunch",
    "servings": 4,
    "tags": ["chicken"],
    "notes": "something easy",
    "is_meal_prep": false
  },
  "preferences": {
    "diet": null,
    "excluded_ingredients": [],
    "max_prep_minutes": 20,
    "max_cook_minutes": 30
  },
  "recent_meals": [...],
  "candidates": [
    {
      "recipe_id": "core_456",
      "title": "Chicken Wrap",
      "tags": ["chicken", "easy"],
      "prep_time": 10,
      "cook_time": 10,
      "summary": "Quick grilled chicken wrap"
    }
  ]
}
```

---

## LLM Output Schema

```json
{
  "selected_recipe_id": "core_456" | null,
  "confidence": 0.0 - 1.0,
  "reason": "Why this recipe fits (optional)",
  "warnings": ["Repeats chicken from yesterday"]
}
```

If `selected_recipe_id` is `null`, the server will treat the slot as a partial failure.

---

## Failure Behavior

- Slot-level failures do not fail the job
- UI should display:
  > "Could not find a recipe fitting your criteria. Try loosening your constraints or adding recipes."

---

## Expected Job Result Shape

Meal plan generation returns an async job payload. The **job result must NOT simply echo the input request**. For each requested meal slot, the server should populate a `selection` when successful.

### Job Envelope (example)

```json
{
  "id": "<job_id>",
  "status": "PENDING|RUNNING|COMPLETE|FAILED",
  "result": {
    "days": [
      {
        "date": "YYYY-MM-DD",
        "meals": {
          "breakfast": {
            "servings": 2,
            "tags": ["easy"],
            "note": null,
            "is_meal_prep": false,
            "repeat": null,
            "selection": {
              "source": "user|core|stage",
              "recipe_id": "<id>"
            }
          }
        }
      }
    ],
    "slot_failures_count": 0
  },
  "error_code": null,
  "error_message": null
}
```

### `selection` Contract

- `selection` is **required** for successful slots.
- `selection` is **null** only when the slot could not be fulfilled.
- `source` indicates where the selected recipe should be fetched from:
  - `user`: user recipe book
  - `core`: stock/core registry
  - `stage`: staged copy created for this user
- `recipe_id` refers to the id within the corresponding source.

### `slot_failures_count`

- `slot_failures_count` counts how many requested slots ended with `selection=null`.
- Slot-level failures must not fail the overall job.

Implementation note: if the current output mirrors the request payload with `selection=null` for every slot, the generation logic is not being executed (or the selection assignment is not being persisted into the job result).

---

## Open Questions / Future Enhancements

- Slot-to-slot dependency reasoning (balance across a full day)
- Explicit diversity weighting configuration
- User-controlled strict vs flexible mode
- Voice-driven pinned recipe selection

---

## Implementation Notes

- LLM should be invoked **per slot**, not per day (v0)
- Model: existing "full" model (e.g. Fireball-Meta-Llama-3.2-8B-Instruct)
- Prompt must explicitly forbid selecting recipes not in candidates
- Server validates LLM output before acting
- The worker must persist the **final computed plan** into the job result (including populated `selection` fields); do not return the original request object.
- Ensure the code path that publishes mailbox completion is using the same computed plan object that contains `selection`.