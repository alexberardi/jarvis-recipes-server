import httpx
import pytest

from jarvis_recipes.app.services.url_recipe_parser import preflight_validate_url


def make_transport(status=200, content_type="text/html"):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_preflight_accepts_html(monkeypatch):
    transport = make_transport(200, "text/html")

    async def client_factory(*args, **kwargs):
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    res = await preflight_validate_url("https://example.com/ok")
    assert res.ok
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_preflight_rejects_bad_status(monkeypatch):
    transport = make_transport(404, "text/html")

    async def client_factory(*args, **kwargs):
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    res = await preflight_validate_url("https://example.com/missing")
    assert not res.ok
    assert res.error_code == "fetch_failed"
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_preflight_rejects_unsupported_content_type(monkeypatch):
    transport = make_transport(200, "application/pdf")

    async def client_factory(*args, **kwargs):
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    res = await preflight_validate_url("https://example.com/file.pdf")
    assert not res.ok
    assert res.error_code == "unsupported_content_type"


@pytest.mark.asyncio
async def test_preflight_blocks_private_host():
    res = await preflight_validate_url("http://localhost/test")
    assert not res.ok
    assert res.error_code == "invalid_url"

