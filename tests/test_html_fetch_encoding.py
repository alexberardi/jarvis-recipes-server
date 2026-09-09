"""Never advertise a content encoding we cannot decode.

fetch_html hardcoded

    "Accept-Encoding": "gzip, deflate, br"

to look browser-shaped. httpx sets that header itself from the codecs actually
available, and brotli was not installed -- so the claim was a lie. Any site that
honours br (most CDNs prefer it) returned brotli-compressed bytes that httpx
handed back undecoded. That arrives as binary, fails the HTML validity check

    has_tags=True, printable_ratio=0.42, control_ratio=0.12
    ValueError: HTML content appears corrupted or invalid encoding

and the import dies -- for a page that served perfectly well. It looked like the
site was blocking us, which sent the whole diagnosis down the wrong path.

The fix is to stop overriding the header and add brotli as a dependency, so
httpx advertises br on its own and can honour it.
"""

import inspect

import httpx
import pytest

from jarvis_recipes.app.services.url_parsing import html_fetcher

HTML = "<html><head><title>Crepes</title></head><body><h1>Crepes</h1></body></html>"


def test_brotli_is_installed():
    """httpx only advertises br when a brotli codec is importable."""
    pytest.importorskip(
        "brotli",
        reason="brotli must be installed; without it httpx cannot decode br responses",
    )


def test_httpx_advertises_only_decodable_encodings():
    advertised = httpx.Client().headers.get("accept-encoding", "")
    codecs = {c.strip() for c in advertised.split(",") if c.strip()}

    assert "br" in codecs, (
        "httpx is not advertising brotli, so the dependency is missing or unwired; "
        f"advertised: {advertised!r}"
    )


def test_fetch_html_does_not_override_accept_encoding():
    """The override is the bug: httpx already knows what it can decode.

    Asserted against the source because the header dict is local to the
    function. Blunt, but it pins the exact mistake rather than a proxy for it.
    """
    source = inspect.getsource(html_fetcher.fetch_html)
    # Comments are allowed to name the header -- the one above the headers dict
    # explains this very bug. Only an actual assignment is the mistake.
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "Accept-Encoding" not in code, (
        "fetch_html sets Accept-Encoding by hand. Let httpx negotiate it, or a "
        "codec we cannot decode gets advertised again."
    )


def test_a_brotli_response_is_actually_decoded():
    """The property that matters, exercised end to end through httpx."""
    brotli = pytest.importorskip("brotli")
    compressed = brotli.compress(HTML.encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        # Mirror what a CDN does when the client advertises br.
        assert "br" in request.headers.get("accept-encoding", "")
        return httpx.Response(
            200,
            content=compressed,
            headers={"Content-Encoding": "br", "Content-Type": "text/html; charset=utf-8"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = client.get("https://example.com/recipe")

    assert response.text == HTML, "brotli response was not decoded"
    assert "<h1>Crepes</h1>" in response.text


def test_an_undecodable_encoding_would_produce_garbage():
    """Why the lie was harmful: the bytes come back raw, not as an error.

    Nothing raises. The caller gets binary that looks like a corrupt page, which
    is why this presented as "the site is blocking us" rather than as a bug.
    """
    brotli = pytest.importorskip("brotli")
    compressed = brotli.compress(HTML.encode("utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        # Content-Encoding httpx has no codec for: it passes the body through.
        return httpx.Response(
            200,
            content=compressed,
            headers={"Content-Encoding": "br-unsupported", "Content-Type": "text/html"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = client.get("https://example.com/recipe")

    assert response.text != HTML
    printable = sum(c.isprintable() or c.isspace() for c in response.text)
    assert printable / max(len(response.text), 1) < 0.9, "expected undecoded binary"
