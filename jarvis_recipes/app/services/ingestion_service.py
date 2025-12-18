import base64
import json
import logging
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup
from httpx import HTTPStatusError

from jarvis_recipes.app.schemas.ingestion_input import ImageRef, IngestionInput
from jarvis_recipes.app.services.url_recipe_parser import (
    ParseResult,
    ParsedRecipe,
    extract_recipe_from_schema_org,
    extract_recipe_heuristic,
    extract_recipe_from_microdata,
    extract_recipe_via_llm,
    fetch_html,
)

logger = logging.getLogger(__name__)

MAX_JSONLD_BLOCKS = 10
MAX_JSONLD_BYTES = 200_000
MAX_HTML_BYTES = 400_000
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 8_000_000


def _load_jsonld_blocks(blocks: List[str]) -> str:
    scripts = []
    for b in blocks[:MAX_JSONLD_BLOCKS]:
        if len(b.encode("utf-8")) > MAX_JSONLD_BYTES:
            continue
        scripts.append(f'<script type="application/ld+json">{b}</script>')
    return "\n".join(scripts)


def _decode_images(images: List[ImageRef]) -> List[bytes]:
    if len(images) > MAX_IMAGES:
        raise ValueError("too_many_images")
    out = []
    for img in images:
        data = base64.b64decode(img.data_base64)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image_too_large")
        out.append(data)
    return out


async def parse_recipe(input: IngestionInput) -> ParseResult:
    # Strategy order: jsonld -> html -> images -> llm
    html: Optional[str] = None
    if input.source_type == "server_fetch":
        if not input.source_url:
            return ParseResult(success=False, error_code="invalid_payload", error_message="source_url required", warnings=[])
        try:
            html = await fetch_html(input.source_url)
        except HTTPStatusError as exc:
            warnings = ["blocked_by_site"] if exc.response is not None and exc.response.status_code in (401, 403) else ["fetch_http_error"]
            return ParseResult(
                success=False,
                error_code="fetch_failed",
                error_message=f"status_{exc.response.status_code if exc.response else 'unknown'}",
                warnings=warnings,
                next_action="webview_extract" if "blocked_by_site" in warnings else None,
                next_action_reason="blocked_by_site" if "blocked_by_site" in warnings else None,
            )
        except httpx.HTTPError as exc:
            return ParseResult(
                success=False,
                error_code="fetch_failed",
                error_message=str(exc),
                warnings=["fetch_http_error"],
            )
    elif input.source_type == "client_webview":
        # build html from provided blocks/snippet
        blocks = input.jsonld_blocks or []
        if len(blocks) > MAX_JSONLD_BLOCKS:
            return ParseResult(success=False, error_code="invalid_payload", error_message="too_many_jsonld_blocks", warnings=[])
        if any(len(b.encode("utf-8")) > MAX_JSONLD_BYTES for b in blocks):
            return ParseResult(success=False, error_code="invalid_payload", error_message="jsonld_block_too_large", warnings=[])
        if input.html_snippet and len(input.html_snippet.encode("utf-8")) > MAX_HTML_BYTES:
            return ParseResult(success=False, error_code="invalid_payload", error_message="html_snippet_too_large", warnings=[])
        html_parts = []
        if blocks:
            html_parts.append(_load_jsonld_blocks(blocks))
        if input.html_snippet:
            html_parts.append(input.html_snippet)
        html = "\n".join(html_parts) if html_parts else None
    elif input.source_type == "image_upload":
        # handled later
        pass
    else:
        return ParseResult(success=False, error_code="invalid_payload", error_message="unknown_source_type", warnings=[])

    # JSON-LD first
    if input.jsonld_blocks:
        if len(input.jsonld_blocks) > MAX_JSONLD_BLOCKS:
            return ParseResult(success=False, error_code="invalid_payload", error_message="too_many_jsonld_blocks", warnings=[])
        if any(len(b.encode("utf-8")) > MAX_JSONLD_BYTES for b in input.jsonld_blocks):
            return ParseResult(success=False, error_code="invalid_payload", error_message="jsonld_block_too_large", warnings=[])
        html_for_jsonld = _load_jsonld_blocks(input.jsonld_blocks)
        parsed = extract_recipe_from_schema_org(html_for_jsonld, input.source_url or "")
        if parsed:
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="client_json_ld", warnings=[])

    # HTML path
    if html:
        parsed = extract_recipe_from_schema_org(html, input.source_url or "")
        if not parsed:
            parsed = extract_recipe_from_microdata(html, input.source_url or "")
        if not parsed:
            parsed = extract_recipe_heuristic(html, input.source_url or "")
        if parsed:
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="client_html", warnings=[])

    # Images (placeholder: not exercising OCR pipeline here)
    if input.images:
        try:
            _decode_images(input.images)
        except ValueError as exc:
            return ParseResult(success=False, error_code="invalid_payload", error_message=str(exc), warnings=[])
        return ParseResult(
            success=False,
            error_code="not_implemented",
            error_message="image_ingestion_not_implemented",
            warnings=[],
        )

    # LLM fallback if we have html
    if html:
        try:
            parsed = await extract_recipe_via_llm(html, input.source_url or "")
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            return ParseResult(success=True, recipe=parsed, used_llm=True, parser_strategy="llm_fallback", warnings=[])
        except Exception as exc:  # noqa: BLE001
            return ParseResult(success=False, error_code="llm_failed", error_message=str(exc), warnings=["llm_failed"])

    return ParseResult(success=False, error_code="invalid_payload", error_message="no content to parse", warnings=[])

