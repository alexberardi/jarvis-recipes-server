"""SSRF-hardening tests for url_parsing.html_fetcher.

This module is a (now-hardened) independent copy of jarvis-web-scraper's
fetcher. Covers: the IPv6/range blocklist, DNS resolution + fail-closed, and the
per-hop redirect revalidation used by preflight (HEAD/GET) and fetch_html.

Hermetic: literal IPs need no DNS; name cases monkeypatch socket.getaddrinfo;
the redirect walk uses a stub client. No network, no DB.
"""

import socket

import httpx
import pytest

from jarvis_recipes.app.services.url_parsing import html_fetcher as h


def _gai(ip: str) -> list:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class _StubClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict, dict | None]] = []

    async def _record(self, method, url, headers=None, cookies=None):
        self.calls.append((method, url, dict(headers or {}), cookies))
        if not self._responses:
            raise AssertionError(f"unexpected {method} {url}")
        return self._responses.pop(0)

    async def head(self, url, headers=None, cookies=None):
        return await self._record("HEAD", url, headers, cookies)

    async def get(self, url, headers=None, cookies=None):
        return await self._record("GET", url, headers, cookies)


def _redirect(location: str, status: int = 302) -> httpx.Response:
    return httpx.Response(status, headers={"location": location})


def _ok() -> httpx.Response:
    return httpx.Response(200, content=b"<html>ok</html>")


_PUBLIC = "http://93.184.216.34/"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254",
        "::1", "::ffff:127.0.0.1", "fe80::1", "64:ff9b::7f00:1",
        "100.64.1.2", "0.0.0.0", "::", "localhost", "localhost.", "LOCALHOST",
    ],
)
def test_blocked_hosts(host: str) -> None:
    assert h.is_private_host(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_allowed_public(host: str) -> None:
    assert h.is_private_host(host) is False


def test_ipv6_loopback_no_longer_bypasses() -> None:
    # Regression vs the old host.split(":")[0] bug.
    assert h.is_private_host("::1") is True


def test_hostname_resolving_private_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _gai("127.0.0.1"))
    assert h.is_private_host("evil.example") is True


def test_unresolvable_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    assert h.is_private_host("nope.invalid") is True


async def test_walk_rejects_redirect_to_loopback() -> None:
    client = _StubClient([_redirect("http://127.0.0.1/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await h._request_following_redirects(client, "HEAD", _PUBLIC, headers={"User-Agent": "x"})
    assert len(client.calls) == 1


async def test_walk_rejects_metadata_and_nonhttp() -> None:
    client = _StubClient([_redirect("http://169.254.169.254/")])
    with pytest.raises(ValueError, match="private or disallowed"):
        await h._request_following_redirects(client, "GET", _PUBLIC, headers={"User-Agent": "x"})

    client = _StubClient([_redirect("gopher://127.0.0.1:6379/")])
    with pytest.raises(ValueError, match="Invalid redirect target"):
        await h._request_following_redirects(client, "GET", _PUBLIC, headers={"User-Agent": "x"})


async def test_walk_normal_chain_succeeds() -> None:
    client = _StubClient([_redirect("http://1.1.1.1/"), _ok()])
    resp = await h._request_following_redirects(client, "GET", _PUBLIC, headers={"User-Agent": "x"})
    assert resp.status_code == 200
    assert len(client.calls) == 2


async def test_walk_cross_origin_strips_cookies() -> None:
    client = _StubClient([_redirect("http://1.1.1.1/"), _ok()])
    await h._request_following_redirects(
        client, "GET", _PUBLIC,
        headers={"User-Agent": "x", "Cookie": "sid=1"},
        cookies={"auth": "token"},
    )
    assert client.calls[0][2].get("Cookie") == "sid=1"
    assert client.calls[0][3] == {"auth": "token"}
    assert "Cookie" not in client.calls[1][2]
    assert client.calls[1][3] is None


async def test_walk_too_many_redirects() -> None:
    client = _StubClient([_redirect("http://1.1.1.1/"), _redirect("http://8.8.8.8/")])
    with pytest.raises(ValueError, match="Too many redirects"):
        await h._request_following_redirects(
            client, "GET", _PUBLIC, headers={"User-Agent": "x"}, max_redirects=1
        )
