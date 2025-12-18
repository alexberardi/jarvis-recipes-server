import pytest

from jarvis_recipes.app.services import url_recipe_parser


def _parsed_recipe():
    return url_recipe_parser.ParsedRecipe(
        title="Parsed Recipe",
        description="From parser",
        source_url="https://example.com",
        image_url=None,
        tags=["tag1"],
        servings=2,
        estimated_time_minutes=25,
        ingredients=[url_recipe_parser.ParsedIngredient(text="1 cup flour")],
        steps=["Mix well"],
    )


def _parse_result_success():
    return url_recipe_parser.ParseResult(
        success=True,
        recipe=_parsed_recipe(),
        used_llm=False,
        parser_strategy="schema_org_json_ld",
        warnings=[],
    )


def test_parse_url_preview(monkeypatch, client, user_token):
    async def fake_parse(url: str, use_llm_fallback: bool = True):
        return _parse_result_success()

    monkeypatch.setattr(url_recipe_parser, "parse_recipe_from_url", fake_parse)

    response = client.post(
        "/recipes/parse-url",
        json={"url": "https://example.com/recipe", "use_llm_fallback": False, "save": False},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["created_recipe_id"] is None
    assert body["recipe"]["title"] == "Parsed Recipe"


def test_parse_url_and_save(monkeypatch, client, user_token):
    async def fake_parse(url: str, use_llm_fallback: bool = True):
        return _parse_result_success()

    monkeypatch.setattr(url_recipe_parser, "parse_recipe_from_url", fake_parse)

    response = client.post(
        "/recipes/parse-url",
        json={"url": "https://example.com/recipe", "use_llm_fallback": False, "save": True},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["created_recipe_id"] is not None
    assert body["recipe"]["title"] == "Parsed Recipe"


def test_parse_url_failure(monkeypatch, client, user_token):
    async def fake_parse(url: str, use_llm_fallback: bool = True):
        return url_recipe_parser.ParseResult(
            success=False,
            recipe=None,
            used_llm=False,
            parser_strategy=None,
            error_code="parse_failed",
            error_message="Unable to parse",
        )

    monkeypatch.setattr(url_recipe_parser, "parse_recipe_from_url", fake_parse)

    response = client.post(
        "/recipes/parse-url",
        json={"url": "https://example.com/recipe", "use_llm_fallback": False, "save": False},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error_code"] == "parse_failed"

