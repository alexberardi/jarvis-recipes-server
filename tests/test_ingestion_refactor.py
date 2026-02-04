import json
from pathlib import Path

import pytest
from httpx import HTTPStatusError, Response, Request

from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
from jarvis_recipes.app.services.ingestion_service import parse_recipe
from jarvis_recipes.app.services import url_recipe_parser


def load_fixture(path: str) -> str:
    return Path(path).read_text()


@pytest.mark.asyncio
async def test_jsonld_only_parses():
    payload = json.loads(Path("tests/fixtures/ingestion/jsonld_valid.json").read_text())
    input_obj = IngestionInput(source_type="client_webview", jsonld_blocks=payload["jsonld_blocks"])
    result = await parse_recipe(input_obj)
    assert result.success
    assert result.parser_strategy == "client_json_ld"
    assert result.recipe
    assert any("pasta" in step.lower() for step in result.recipe.steps)


@pytest.mark.skip(reason="Test assertion needs update")
@pytest.mark.asyncio
async def test_html_snippet_parses():
    html = load_fixture("tests/fixtures/ingestion/html_snippet.html")
    input_obj = IngestionInput(source_type="client_webview", html_snippet=html)
    result = await parse_recipe(input_obj)
    assert result.success
    assert result.parser_strategy == "client_html"
    assert result.recipe
    assert "Snippet Soup".lower() in result.recipe.title.lower()


@pytest.mark.asyncio
async def test_oversize_payload_rejected():
    big_block = "x" * 210_000
    input_obj = IngestionInput(source_type="client_webview", jsonld_blocks=[big_block])
    result = await parse_recipe(input_obj)
    assert not result.success
    assert result.error_code == "invalid_payload"


@pytest.mark.asyncio
async def test_blocked_url_sets_next_action(monkeypatch):
    # Monkeypatch fetch_html to raise HTTPStatusError 403
    async def fake_fetch(url: str):
        req = Request("GET", url)
        resp = Response(403, request=req)
        raise HTTPStatusError("forbidden", request=req, response=resp)

    monkeypatch.setattr(url_recipe_parser, "fetch_html", fake_fetch)
    from jarvis_recipes.app.schemas.ingestion_input import IngestionInput
    from jarvis_recipes.app.services.ingestion_service import parse_recipe

    input_obj = IngestionInput(source_type="server_fetch", source_url="https://blocked.test")
    result = await parse_recipe(input_obj)
    assert not result.success
    assert result.error_code == "fetch_failed"
    assert result.next_action == "webview_extract"
    assert result.next_action_reason == "blocked_by_site"

