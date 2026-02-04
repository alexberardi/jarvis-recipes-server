import httpx
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from jarvis_recipes.app.services.url_recipe_parser import preflight_validate_url


@pytest.mark.asyncio
async def test_preflight_accepts_html():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await preflight_validate_url("https://example.com/ok")
        assert res.ok
        assert res.status_code == 200


@pytest.mark.asyncio
async def test_preflight_rejects_bad_status():
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.headers = {"content-type": "text/html"}

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await preflight_validate_url("https://example.com/missing")
        assert not res.ok
        assert res.error_code == "fetch_failed"
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_preflight_rejects_unsupported_content_type():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/pdf"}

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        res = await preflight_validate_url("https://example.com/file.pdf")
        assert not res.ok
        assert res.error_code == "unsupported_content_type"


@pytest.mark.asyncio
async def test_preflight_blocks_private_host():
    res = await preflight_validate_url("http://localhost/test")
    assert not res.ok
    assert res.error_code == "invalid_url"
