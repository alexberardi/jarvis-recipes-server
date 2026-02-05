"""HTML fetching and URL validation utilities."""

import ipaddress
import json
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.services.url_parsing.models import PreflightResult

logger = logging.getLogger(__name__)


def is_private_host(host: str) -> bool:
    """Check if a host is private/localhost."""
    hostname = host.split(":")[0]
    try:
        ip = ipaddress.ip_address(hostname)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return hostname.lower() in {"localhost"}


async def preflight_validate_url(url: str, timeout: float = 3.0) -> PreflightResult:
    """Cheap preflight to guard enqueue. HEAD first, fallback to GET on 405."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return PreflightResult(
            ok=False,
            error_code="invalid_url",
            error_message="URL must start with http or https.",
        )
    if not parsed.netloc or is_private_host(parsed.hostname or ""):
        return PreflightResult(
            ok=False,
            error_code="invalid_url",
            error_message="Host is blocked (localhost/private).",
        )

    settings = get_settings()
    headers = {
        "User-Agent": settings.scraper_user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    cookies = {}
    if settings.scraper_cookies:
        try:
            cookies = json.loads(settings.scraper_cookies)
        except json.JSONDecodeError:
            cookies = {}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        resp = None
        try:
            resp = await client.head(url, headers=headers, cookies=cookies)
            if resp.status_code == 405:
                resp = await client.get(url, headers=headers, cookies=cookies)
        except httpx.ConnectTimeout:
            return PreflightResult(
                ok=False,
                error_code="fetch_timeout",
                error_message="Timed out connecting to the site.",
            )
        except httpx.ReadTimeout:
            return PreflightResult(
                ok=False,
                error_code="fetch_timeout",
                error_message="Timed out reading from the site.",
            )
        except httpx.HTTPError as exc:
            return PreflightResult(
                ok=False,
                error_code="fetch_failed",
                error_message=f"Network error: {exc}",
            )

    ctype = resp.headers.get("content-type", "")
    if resp.status_code >= 400:
        is_blocked = resp.status_code in (401, 403)
        return PreflightResult(
            ok=False,
            status_code=resp.status_code,
            content_type=ctype,
            error_code="fetch_failed",
            error_message=f"Site returned status {resp.status_code}.",
            next_action="webview_extract" if is_blocked else None,
            next_action_reason="blocked_by_site" if is_blocked else None,
        )
    if "text/html" not in ctype and "application/xhtml" not in ctype and ctype:
        return PreflightResult(
            ok=False,
            status_code=resp.status_code,
            content_type=ctype,
            error_code="unsupported_content_type",
            error_message=f"Unsupported content type: {ctype}",
        )

    # For successful responses, fetch a small sample to check encoding
    if resp.status_code == 200:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                sample_resp = await client.get(
                    url,
                    headers=headers,
                    cookies=cookies,
                )
                content_bytes = sample_resp.content[:5000]

            encoding = None
            if "charset=" in ctype.lower():
                try:
                    encoding = ctype.split("charset=")[1].split(";")[0].strip().strip('"\'')
                except (IndexError, AttributeError):
                    pass

            if not encoding:
                encoding = "utf-8"

            try:
                text_sample = content_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                try:
                    text_sample = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    return PreflightResult(
                        ok=False,
                        status_code=resp.status_code,
                        content_type=ctype,
                        error_code="encoding_error",
                        error_message="Unable to decode HTML content with detected encoding",
                        next_action="webview_extract",
                        next_action_reason="encoding_error",
                    )

            if len(text_sample) > 100:
                has_html_tags = bool(re.search(r"<[a-z]+[^>]*>", text_sample[:2000], re.I))
                printable_count = sum(
                    1 for c in text_sample[:2000] if (32 <= ord(c) <= 126) or c.isspace()
                )
                printable_ratio = (
                    printable_count / min(len(text_sample[:2000]), 2000)
                    if text_sample[:2000]
                    else 0
                )
                control_chars = sum(
                    1 for c in text_sample[:2000] if ord(c) < 32 and c not in "\n\r\t"
                )
                control_ratio = (
                    control_chars / min(len(text_sample[:2000]), 2000)
                    if text_sample[:2000]
                    else 0
                )

                if not has_html_tags or printable_ratio < 0.6 or control_ratio > 0.1:
                    logger.warning(
                        "Preflight detected encoding/corruption issue for %s: has_tags=%s, printable=%.2f, control=%.2f",
                        url,
                        has_html_tags,
                        printable_ratio,
                        control_ratio,
                    )
                    return PreflightResult(
                        ok=False,
                        status_code=resp.status_code,
                        content_type=ctype,
                        error_code="encoding_error",
                        error_message="HTML content appears corrupted or has encoding issues",
                        next_action="webview_extract",
                        next_action_reason="encoding_error",
                    )
        except Exception as exc:
            logger.warning("Preflight encoding check failed for %s: %s", url, exc)

    return PreflightResult(ok=True, status_code=resp.status_code, content_type=ctype)


async def fetch_html(url: str) -> str:
    """Fetch HTML content from a URL with encoding handling and fallbacks."""
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Invalid URL")
    if is_private_host(parsed_url.hostname or ""):
        raise ValueError("URL points to a private or disallowed host")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.google.com/",
        "Connection": "keep-alive",
    }
    cookie_env = os.getenv("SCRAPER_COOKIES")
    if cookie_env:
        headers["Cookie"] = cookie_env
    timeout = httpx.Timeout(15.0, read=15.0, connect=5.0)

    async def _try_fetch(
        target_url: str, extra_headers: Optional[dict] = None
    ) -> httpx.Response:
        merged_headers = headers | (extra_headers or {})
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=merged_headers
        ) as client:
            return await client.get(target_url)

    try:
        response = await _try_fetch(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            try:
                alt_headers = {"Accept": "*/*"}
                response = await _try_fetch(url, alt_headers)
                response.raise_for_status()
            except httpx.HTTPStatusError:
                proxy_url = f"https://r.jina.ai/{url}"
                response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
                response.raise_for_status()
        else:
            raise
    except (httpx.RequestError, httpx.TimeoutException):
        proxy_url = f"https://r.jina.ai/{url}"
        response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise ValueError(f"Unsupported content type: {content_type}")

    # Handle encoding
    try:
        content_bytes = response.content

        encoding = None
        if "charset=" in content_type.lower():
            try:
                encoding = (
                    content_type.split("charset=")[1].split(";")[0].strip().strip("\"'")
                )
            except (IndexError, AttributeError):
                pass

        if not encoding:
            encoding = "utf-8"

        try:
            text = content_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            try:
                text = content_bytes.decode("utf-8", errors="replace")
                encoding_match = re.search(
                    r'<meta[^>]+charset=["\']?([^"\'>\s]+)', text, re.I
                )
                if encoding_match:
                    detected_encoding = encoding_match.group(1).lower()
                    if detected_encoding and detected_encoding != "utf-8":
                        try:
                            text = content_bytes.decode(detected_encoding)
                        except (UnicodeDecodeError, LookupError):
                            pass
            except (UnicodeDecodeError, LookupError):
                text = response.text

        # Validate text
        if text and len(text) > 100:
            has_html_tags = bool(re.search(r"<[a-z]+[^>]*>", text[:2000], re.I))
            sample = text[:2000]
            printable_count = sum(
                1 for c in sample if (32 <= ord(c) <= 126) or c.isspace()
            )
            printable_ratio = printable_count / len(sample) if sample else 0
            control_chars = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
            control_ratio = control_chars / len(sample) if sample else 0

            if has_html_tags and printable_ratio > 0.6 and control_ratio < 0.1:
                return text
            else:
                logger.warning(
                    "HTML validation failed for %s: has_tags=%s, printable_ratio=%.2f, control_ratio=%.2f",
                    url,
                    has_html_tags,
                    printable_ratio,
                    control_ratio,
                )
                raise ValueError("HTML content appears corrupted or invalid encoding")

        text_fallback = response.text
        if text_fallback and len(text_fallback) > 100:
            has_html_tags = bool(
                re.search(r"<[a-z]+[^>]*>", text_fallback[:2000], re.I)
            )
            if has_html_tags:
                return text_fallback

        raise ValueError("Unable to decode HTML content with valid encoding")
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("Encoding error when fetching %s: %s. Attempting fallback.", url, exc)
        try:
            text_fallback = response.text
            if text_fallback and len(text_fallback) > 100:
                has_html_tags = bool(
                    re.search(r"<[a-z]+[^>]*>", text_fallback[:2000], re.I)
                )
                if has_html_tags:
                    return text_fallback
        except (UnicodeDecodeError, AttributeError):
            pass
        raise ValueError(f"HTML content encoding error: {exc}")
