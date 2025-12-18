# PRD: Jarvis Recipes — FastAPI Recipes & Meal Planning Service (MVP + Dockerized)

This document defines the requirements for the **Jarvis Recipes** backend service.

It provides:
- Recipe storage + CRUD
- Local media upload with future-proof abstraction for S3
- Import endpoints (URL/photo) with mocked parsing
- Meal planning endpoints (stubbed for now)
- Multi-user support (each recipe/plan is owned by a user)
- JWT‑protected access (tokens issued by jarvis-auth)
- Docker deployment (FastAPI + Postgres)
- Database migrations via Alembic
- Unit tests for core features

This MVP must implement all endpoints and DB structure, but may return static/mock content for imports & planning.

---

## Tech Stack

- Python 3.11+
- FastAPI
- SQLAlchemy ORM + Alembic
- Postgres (via Docker Compose)
- JWT validation using python-jose or pyjwt
- Pydantic for request/response models
- pytest for testing
- Storage provider abstraction for images (local first)

---

## Authentication

All business endpoints **require** a valid JWT in the header:

```
Authorization: Bearer <access_token>
```

A dependency `get_current_user()` will:
- Decode + validate token
- Extract user_id from the `sub` claim
- Reject missing/invalid tokens with 401

No user creation or password logic lives here — handled by jarvis-auth.

---

## Database Schema

**Users**
- user_id referenced from JWT `sub`
- DO NOT store passwords here

**Recipes**
- id (PK)
- user_id (FK)
- title
- description (optional)
- image_url (optional)
- source_type: "manual" | "image" | "url"
- source_url (optional)
- servings (optional)
- total_time_minutes (optional)
- created_at
- updated_at

**Ingredients**
- id (PK)
- recipe_id (FK)
- text

**Steps**
- id (PK)
- recipe_id (FK)
- step_number
- text

**Tags**
- id (PK)
- name (unique)

**RecipeTags** (many-to-many join)
- recipe_id
- tag_id

**MealPlan**
- id (PK)
- user_id (FK)
- name (optional)
- start_date
- created_at

**MealPlanItem**
- id (PK)
- meal_plan_id (FK)
- recipe_id (FK)
- date
- meal_type (e.g., "breakfast" | "lunch" | "dinner")

---

## Endpoints

🔒 means JWT protected

### Recipes

**POST /recipes** 🔒  
Create a recipe (manual version).  
Body: title, description, servings, steps, ingredients, tags

**GET /recipes** 🔒  
Return list scoped to current user.

**GET /recipes/{id}** 🔒  
Return full details.

**PATCH /recipes/{id}** 🔒  
Partial update.

**DELETE /recipes/{id}** 🔒  
Delete recipe + cascades sub-data.

---

### Tags

**GET /tags** 🔒  
Return all tags for current user (tags from their recipes).

**POST /tags** 🔒  
Create a new tag.

**POST /recipes/{recipe_id}/tags/{tag_id}** 🔒  
Attach tag.

**DELETE /recipes/{recipe_id}/tags/{tag_id}** 🔒  
Remove tag.

---

### Import (Mocked for MVP)

**POST /recipes/import/image** 🔒  
Multipart image upload.  
Flow:
- Save image via StorageProvider (local → `/media/…`)
- Return **mock** draft JSON:
```
{
  "title": "...",
  "ingredients": [...],
  "steps": [...],
  "tags": []
}
```

**POST /recipes/import/url** 🔒  
Body: `{ "url": "https://example.com" }`  
Return mock draft JSON same as above.

---

### Planner (Stubbed for MVP)

**POST /planner/draft** 🔒  
Body: date range + preferences  
Return mocked staged plan.

**POST /planner/commit** 🔒  
Store finalized plan + items.

**GET /planner/current** 🔒  
Return current plan or empty.

---

## File Storage Abstraction

Create a provider interface:
```
save_image(file) -> str  # returns public URL
delete_image(url: str) -> None
```

Initial implementation:
- Store files under `/media/`
- Returned URLs must map to static serving path `/media/<filename>`

Future providers:
- S3 or MinIO possible by swapping the provider class via config.

---

## Docker Requirements

Repo must contain:
- `Dockerfile`
- `docker-compose.yml`
- `alembic.ini`
- `.env.example` with required variables

**docker-compose** services:
- `recipes-api`: FastAPI app (expose port 8001)
- `db`: Postgres

Volume:
- `./media:/app/media` (persistent local recipe photos)

Startup behavior:
- Auto-run Alembic migrations before starting API.

README must include:
```
docker-compose up --build
# API at http://localhost:8001/docs
``**

---

## Testing Requirements

Using pytest + TestClient:

Tests must verify:

1️⃣ Unauthorized access → 401  
2️⃣ Recipe create → saved to DB and tied to current user  
3️⃣ Recipe list → returns ONLY that user’s recipes  
4️⃣ Image import → returns mock draft structure  
5️⃣ Planner draft → returns mock structure

SQLite acceptable for tests.

---

## Folder Structure (Required)

```
jarvis_recipes/
  app/
    main.py
    api/
      deps.py
      routes/
        recipes.py
        tags.py
        import.py
        planner.py
    db/
      base.py
      session.py
      models.py
    schemas/
      recipe.py
      planner.py
      tag.py
      auth.py
    services/
      recipes_service.py
      planner_service.py
      storage/
        base.py
        local.py
  alembic/
    env.py
    versions/
  tests/
    test_recipes.py
    test_auth_required.py
  Dockerfile
  docker-compose.yml
  alembic.ini
  requirements.txt or pyproject.toml
  .env.example
  README.md
  media/
```

---

## Development Principles

- JWT **required** on all CRUD/select endpoints
- DB filtering always by `user_id`
- Clean separation of concerns (db, schemas, api, services, storage)
- Minimal business logic in route handlers
- Stop after MVP endpoints + structure are done
- Confirm with stakeholder before adding real OCR/LLM or planner logic

---

## MVP Deliverables

When complete, this service must:

- Build + run via `docker-compose up --build`
- Pass all tests
- Support auth‑protected CRUD recipes + tags
- Support mock image/URL parsing for imports
- Support stubbed planner endpoints
- Persist recipe & planning data to Postgres
- Serve local media from `/media/`

After delivery, **pause** and request review before:
- Real OCR/LLM parsing
- S3 integration
- Full planner AI

---

**END OF SPEC — FOLLOW EXACTLY**
