import base64
import json
import logging
import re
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
    clean_soup_for_content,
    find_main_node,
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
        except ValueError as exc:
            # Check if this is an encoding/corruption error (not just invalid URL)
            error_msg = str(exc)
            is_encoding_error = "encoding" in error_msg.lower() or "corrupted" in error_msg.lower() or "invalid encoding" in error_msg.lower()
            
            if is_encoding_error:
                logger.warning("Encoding/corruption error for %s: %s. Suggesting webview fallback.", input.source_url, error_msg)
                return ParseResult(
                    success=False,
                    error_code="fetch_failed",
                    error_message=error_msg,
                    warnings=["encoding_error"],
                    next_action="webview_extract",
                    next_action_reason="encoding_error",
                )
            else:
                # Regular invalid URL error
                return ParseResult(
                    success=False,
                    error_code="invalid_url",
                    error_message=error_msg,
                    warnings=[],
                )
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
        
        # Clean HTML snippet if provided - use existing cleaning functions
        cleaned_html_snippet = None
        if input.html_snippet:
            try:
                soup = BeautifulSoup(input.html_snippet, "lxml")
                # Use existing cleaning function to remove boilerplate
                clean_soup_for_content(soup)
                # Use existing function to find main content node
                main_node = find_main_node(soup)
                if main_node:
                    cleaned_html_snippet = str(main_node)
                else:
                    # Fallback to body if no main node found
                    cleaned_html_snippet = str(soup.body) if soup.body else str(soup)
                # Limit size to avoid overwhelming LLM (100KB is plenty)
                if len(cleaned_html_snippet.encode("utf-8")) > 100_000:
                    cleaned_html_snippet = cleaned_html_snippet[:100_000]
                logger.info("Cleaned HTML snippet: %d bytes -> %d bytes", 
                          len(input.html_snippet.encode("utf-8")), 
                          len(cleaned_html_snippet.encode("utf-8")))
            except Exception as exc:
                logger.warning("Failed to clean HTML snippet: %s, using raw", exc)
                cleaned_html_snippet = input.html_snippet[:100_000] if len(input.html_snippet.encode("utf-8")) > 100_000 else input.html_snippet
        
        html_parts = []
        if blocks:
            html_parts.append(_load_jsonld_blocks(blocks))
        if cleaned_html_snippet:
            html_parts.append(cleaned_html_snippet)
        html = "\n".join(html_parts) if html_parts else None
    elif input.source_type == "image_upload":
        # handled later
        pass
    else:
        return ParseResult(success=False, error_code="invalid_payload", error_message="unknown_source_type", warnings=[])

    # JSON-LD first (most reliable)
    if input.jsonld_blocks:
        logger.info("Attempting JSON-LD extraction with %d blocks", len(input.jsonld_blocks))
        # Log first block to see what we're getting (truncated for safety)
        if input.jsonld_blocks:
            first_block = input.jsonld_blocks[0]
            block_preview = first_block[:500] if len(first_block) > 500 else first_block
            logger.info("First JSON-LD block preview (first 500 chars): %s", block_preview)
            # Try to parse and see what @type it has
            try:
                import json
                data = json.loads(first_block)
                if isinstance(data, dict):
                    obj_type = data.get("@type")
                    logger.info("First JSON-LD block @type: %s", obj_type)
                elif isinstance(data, list) and len(data) > 0:
                    first_item = data[0]
                    if isinstance(first_item, dict):
                        obj_type = first_item.get("@type")
                        logger.info("First JSON-LD block is a list, first item @type: %s", obj_type)
            except Exception as exc:
                logger.warning("Could not parse first JSON-LD block to check @type: %s", exc)
        
        if len(input.jsonld_blocks) > MAX_JSONLD_BLOCKS:
            return ParseResult(success=False, error_code="invalid_payload", error_message="too_many_jsonld_blocks", warnings=[])
        if any(len(b.encode("utf-8")) > MAX_JSONLD_BYTES for b in input.jsonld_blocks):
            return ParseResult(success=False, error_code="invalid_payload", error_message="jsonld_block_too_large", warnings=[])
        html_for_jsonld = _load_jsonld_blocks(input.jsonld_blocks)
        parsed = extract_recipe_from_schema_org(html_for_jsonld, input.source_url or "")
        if parsed:
            logger.info("Successfully extracted recipe from JSON-LD")
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="client_json_ld", warnings=[])
        else:
            logger.warning("JSON-LD extraction failed - no valid recipe found in %d blocks", len(input.jsonld_blocks))
    else:
        logger.warning("No JSON-LD blocks provided by client - this is suboptimal, should extract JSON-LD from webview")

    # HTML path (try structured extraction before LLM)
    if html:
        logger.info("Attempting HTML extraction (schema_org -> microdata -> heuristic)")
        parsed = extract_recipe_from_schema_org(html, input.source_url or "")
        if not parsed:
            parsed = extract_recipe_from_microdata(html, input.source_url or "")
        if not parsed:
            parsed = extract_recipe_heuristic(html, input.source_url or "")
        if parsed:
            logger.info("Successfully extracted recipe from HTML using %s", "schema_org" if parsed else "heuristic")
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="client_html", warnings=[])
        else:
            logger.warning("All HTML extraction strategies failed, falling back to LLM")

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
        logger.info("Attempting LLM extraction (last resort) for %s", input.source_url)
        try:
            parsed = await extract_recipe_via_llm(html, input.source_url or "")
            parsed.ingredients = parsed.ingredients or []
            parsed.steps = parsed.steps or []
            parsed.notes = parsed.notes or []
            logger.info("Successfully extracted recipe via LLM")
            return ParseResult(success=True, recipe=parsed, used_llm=True, parser_strategy="llm_fallback", warnings=[])
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM extraction failed for %s: %s", input.source_url, exc)
            return ParseResult(success=False, error_code="llm_failed", error_message=str(exc), warnings=["llm_failed"])

    return ParseResult(success=False, error_code="invalid_payload", error_message="no content to parse", warnings=[])

