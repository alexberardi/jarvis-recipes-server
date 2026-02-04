import json

import pytest

from jarvis_recipes.app.services import url_recipe_parser


def test_extract_recipe_from_schema_org():
    html = """
    <html>
      <head>
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Recipe",
          "name": "Test Recipe",
          "recipeIngredient": ["1 cup flour", "2 eggs"],
          "recipeInstructions": ["Mix", "Bake"],
          "totalTime": "PT30M",
          "recipeYield": "4"
        }
        </script>
      </head>
    </html>
    """

    parsed = url_recipe_parser.extract_recipe_from_schema_org(html, "https://example.com/test")
    assert parsed is not None
    assert parsed.title == "Test Recipe"
    assert parsed.estimated_time_minutes == 30
    assert parsed.servings == 4
    assert len(parsed.ingredients) == 2
    assert parsed.steps == ["Mix", "Bake"]


def test_extract_recipe_heuristic():
    html = """
    <html>
      <body>
        <h1>Heuristic Soup</h1>
        <article>
          <ul>
            <li>1 cup broth</li>
            <li>2 tsp salt</li>
          </ul>
          <h2>Directions</h2>
          <ol>
            <li>Heat the broth.</li>
            <li>Add salt.</li>
          </ol>
        </article>
      </body>
    </html>
    """
    parsed = url_recipe_parser.extract_recipe_heuristic(html, "https://example.com/soup")
    assert parsed is not None
    assert parsed.title == "Heuristic Soup"
    assert len(parsed.ingredients) == 2
    assert parsed.steps[0].startswith("Heat")


@pytest.mark.asyncio
async def test_extract_recipe_via_llm(monkeypatch):
    settings = url_recipe_parser.get_settings()
    settings.llm_base_url = "http://llm-proxy"
    settings.jarvis_auth_app_id = "app-id"
    settings.jarvis_auth_app_key = "app-key"

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str):
            self._content = content
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": self._content,
                        }
                    }
                ]
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            payload = {
                "title": "LLM Recipe",
                "description": "LLM generated",
                "source_url": "https://example.com",
                "image_url": None,
                "tags": [],
                "servings": 2,
                "estimated_time_minutes": 15,
                "ingredients": [{"text": "1 cup rice"}],
                "steps": ["Cook rice"],
                "notes": [],
            }
            return FakeResponse(json.dumps(payload))

    monkeypatch.setattr(url_recipe_parser.httpx, "AsyncClient", FakeAsyncClient)

    parsed = await url_recipe_parser.extract_recipe_via_llm("<html><body>Some content</body></html>", "https://example.com", {})
    assert parsed.title == "LLM Recipe"
    assert parsed.steps == ["Cook rice"]
    assert parsed.source_url == "https://example.com"


@pytest.mark.asyncio
async def test_parse_recipe_from_url_schema_first(monkeypatch):
    html = """
    <script type="application/ld+json">
        {"@type":"Recipe","name":"Schema Dish","recipeIngredient":["1 egg"],"recipeInstructions":["Boil egg"]}
    </script>
    """

    async def fake_fetch(url: str):
        return html

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)

    result = await url_recipe_parser.parse_recipe_from_url("https://example.com/schema", use_llm_fallback=False)
    assert result.success is True
    assert result.used_llm is False
    assert result.parser_strategy == "schema_org_json_ld"


@pytest.mark.asyncio
async def test_parse_recipe_from_url_heuristic(monkeypatch):
    html = """
    <html><body><h1>Heuristic Dish</h1><article><ul><li>1 cup milk</li><li>2 tbsp sugar</li></ul>
    <ol><li>Step one</li></ol></article></body></html>
    """

    async def fake_fetch(url: str):
        return html

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)

    result = await url_recipe_parser.parse_recipe_from_url("https://example.com/heuristic", use_llm_fallback=False)
    assert result.success is True
    assert result.parser_strategy == "heuristic"
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_parse_recipe_from_url_llm(monkeypatch):
    settings = url_recipe_parser.get_settings()
    settings.llm_base_url = "http://llm-proxy"
    settings.jarvis_auth_app_id = "app-id"
    settings.jarvis_auth_app_key = "app-key"

    async def fake_fetch(url: str):
        return "<html><body>No recipe</body></html>"

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str):
            self._content = content
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": self._content}}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            payload = {
                "title": "LLM Dish",
                "ingredients": [{"text": "1 cup water"}],
                "steps": ["Mix"],
                "tags": [],
                "notes": [],
                "servings": None,
                "estimated_time_minutes": None,
                "image_url": None,
                "source_url": "https://example.com",
                "description": None,
            }
            return FakeResponse(json.dumps(payload))

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_from_schema_org", lambda html, url: None)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_heuristic", lambda html, url: None)
    monkeypatch.setattr(url_recipe_parser.httpx, "AsyncClient", FakeAsyncClient)

    result = await url_recipe_parser.parse_recipe_from_url("https://example.com/llm", use_llm_fallback=True)
    assert result.success is True
    assert result.used_llm is True
    assert result.parser_strategy == "llm_fallback"


@pytest.mark.asyncio
async def test_parse_llm_json_with_noise(monkeypatch):
    settings = url_recipe_parser.get_settings()
    settings.llm_base_url = "http://llm-proxy"
    settings.jarvis_auth_app_id = "app-id"
    settings.jarvis_auth_app_key = "app-key"

    async def fake_fetch(url: str):
        return "<html><body>No recipe</body></html>"

    noisy_content = """
    some preamble text that is not json
    {
      "title": "Noisy Dish",
      "ingredients": [{"text": "water"}],
      "steps": ["Boil water"],
      "tags": [],
      "notes": [],
      "servings": null,
      "estimated_time_minutes": null,
      "image_url": null,
      "source_url": "https://example.com",
      "description": null
    }
    trailing text
    """

    class FakeResponse:
        status_code = 200

        def __init__(self, content: str):
            self._content = content
            self.headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": noisy_content}}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse("")

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_from_schema_org", lambda html, url: None)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_heuristic", lambda html, url: None)
    monkeypatch.setattr(url_recipe_parser.httpx, "AsyncClient", FakeAsyncClient)

    result = await url_recipe_parser.parse_recipe_from_url("https://example.com/noisy", use_llm_fallback=True)
    assert result.success is True
    assert result.used_llm is True
    assert result.recipe
    assert result.recipe.title == "Noisy Dish"


@pytest.mark.asyncio
async def test_parse_recipe_from_url_all_fail(monkeypatch):
    async def fake_fetch(url: str):
        return "<html><body>No recipe here</body></html>"

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_from_schema_org", lambda html, url: None)
    monkeypatch.setattr(url_recipe_parser, "extract_recipe_heuristic", lambda html, url: None)

    result = await url_recipe_parser.parse_recipe_from_url("https://example.com/fail", use_llm_fallback=False)
    assert result.success is False
    assert result.error_code == "parse_failed"

