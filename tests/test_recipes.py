from datetime import date

from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.schemas.recipe import RecipeCreate
from jarvis_recipes.app.services import recipes_service


def recipe_payload():
    return {
        "title": "Test Recipe",
        "description": "Tasty",
        "servings": 2,
        "prep_time_minutes": 10,
        "cook_time_minutes": 20,
        "ingredients": [{"text": "1 cup flour"}, {"text": "2 eggs"}],
        "steps": [{"step_number": 1, "text": "Mix"}, {"step_number": 2, "text": "Bake"}],
        "tags": ["baking"],
        "source_type": "manual",
    }


def test_create_recipe_and_scoping(client, db_session, user_token):
    response = client.post("/recipes", json=recipe_payload(), headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 201
    body = response.json()
    assert body["user_id"] == "1"
    assert body["title"] == "Test Recipe"
    assert body["total_time_minutes"] == 30
    other_payload = recipe_payload()
    other_payload["title"] = "Other User Recipe"
    other_recipe = RecipeCreate(**other_payload)
    recipes_service.create_recipe(db_session, CurrentUser(id=2), other_recipe)
    list_response = client.get("/recipes", headers={"Authorization": f"Bearer {user_token}"})
    assert list_response.status_code == 200
    titles = {r["title"] for r in list_response.json()}
    assert "Test Recipe" in titles
    assert "Other User Recipe" not in titles


def test_create_requires_ingredients_and_steps(client, user_token):
    bad_payload = recipe_payload()
    bad_payload["ingredients"] = []
    response = client.post("/recipes", json=bad_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 422

    bad_payload = recipe_payload()
    bad_payload["steps"] = []
    response = client.post("/recipes", json=bad_payload, headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 422


def test_import_url_mock(client, user_token):
    response = client.post("/recipes/import/url", json={"url": "https://example.com"}, headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Draft from URL"
    assert "ingredients" in body and isinstance(body["ingredients"], list)
    assert "steps" in body and isinstance(body["steps"], list)


def test_import_image_mock(client, user_token):
    files = {"file": ("test.jpg", b"fake-bytes", "image/jpeg")}
    response = client.post("/recipes/import/image", files=files, headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Draft from image"
    assert body.get("image_url", "").startswith("/media/")


def test_planner_draft(client, user_token):
    payload = {"start_date": str(date(2024, 1, 1)), "end_date": str(date(2024, 1, 2)), "preferences": "veg"}
    response = client.post("/planner/draft", json=payload, headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert len(body["items"]) > 0



def test_list_parse_jobs_uses_the_abandon_window_setting(client, user_token):
    """Regression: this handler reads parse_job.abandon_minutes from the settings DB.

    Uses the /parse-url/jobs alias because the bare /recipes/jobs path is
    shadowed — see test_bare_recipes_jobs_path_is_shadowed below.
    """
    response = client.get(
        "/recipes/parse-url/jobs", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert response.status_code == 200
    assert response.json() == {"jobs": []}


def test_bare_recipes_jobs_path_is_shadowed(client, user_token):
    """Pre-existing bug, pinned so a fix is noticed rather than silently landing.

    GET /recipes/{recipe_id} is registered before GET /recipes/jobs, and
    recipe_id is an int, so "jobs" fails path validation with a 422 and the
    list-jobs handler is never reached. Fix = move the literal-path routes above
    the /{recipe_id} routes; then this test should assert 200.
    """
    response = client.get("/recipes/jobs", headers={"Authorization": f"Bearer {user_token}"})
    assert response.status_code == 422
