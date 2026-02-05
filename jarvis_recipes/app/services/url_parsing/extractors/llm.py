"""LLM-based recipe extraction."""

import hashlib
import json
import logging
import re
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.services.llm_client import (
    _repair_json_via_full_llm,
    _try_local_json_repair,
)
from jarvis_recipes.app.services.url_parsing.extractors.heuristic import (
    _find_ingredient_items,
    _find_instruction_items,
    clean_soup_for_content,
    find_main_node,
)
from jarvis_recipes.app.services.url_parsing.models import ParsedRecipe
from jarvis_recipes.app.services.url_parsing.parsing_utils import clean_text

logger = logging.getLogger(__name__)


def _parse_llm_json_content(raw: str) -> dict:
    """Parse LLM content into JSON, handling common formatting issues."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw.strip()
        # Strip markdown code fences if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*", "", cleaned).strip()
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
    raise ValueError("LLM response was not valid JSON")


def _build_llm_content(html: str) -> Tuple[Optional[str], str]:
    """Build truncated content for LLM processing."""
    # Safety check for corrupted HTML
    if html and len(html) > 100:
        sample = html[:2000]
        printable_count = sum(
            1 for c in sample if (32 <= ord(c) <= 126) or c.isspace()
        )
        printable_ratio = printable_count / len(sample) if sample else 0
        control_chars = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
        control_ratio = control_chars / len(sample) if sample else 0

        if printable_ratio < 0.5 or control_ratio > 0.15:
            logger.warning(
                "Detected corrupted HTML in _build_llm_content: printable_ratio=%.2f, control_ratio=%.2f",
                printable_ratio,
                control_ratio,
            )
            raise ValueError("HTML content appears corrupted - encoding error detected")

    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("h1") or soup.title
    title = clean_text(title_tag.get_text()) if title_tag else None

    # Capture JSON-LD scripts before cleaning
    script_texts: List[str] = []
    scripts = soup.find_all("script", type="application/ld+json")
    for sc in scripts:
        txt = sc.get_text(strip=True)
        if txt:
            script_texts.append(txt[:2000])

    clean_soup_for_content(soup)
    main_node = find_main_node(soup)
    if not main_node:
        combined = "\n".join(script_texts)[:6000]
        return title, combined

    ingredients = _find_ingredient_items(main_node)
    instructions = _find_instruction_items(main_node)

    parts: List[str] = []
    if ingredients:
        parts.append("Ingredients:\n" + "\n".join(ingredients))
    if instructions:
        parts.append("Instructions:\n" + "\n".join(instructions))

    # Fallback: include main text if parts are thin
    if len("\n\n".join(parts)) < 500:
        text = main_node.get_text("\n", strip=True)
        text = re.sub(r"\n{2,}", "\n", text)
        lines = text.splitlines()
        parts.append("\n".join(lines[:200]))

    combined = (title + "\n" if title else "") + "\n\n".join([p for p in parts if p])
    combined = combined[:10000]
    return title, combined


async def extract_recipe_via_llm(
    html: str, url: str, metadata: Optional[dict] = None
) -> ParsedRecipe:
    """Extract recipe using LLM when structured parsing fails."""
    settings = get_settings()
    if not settings.llm_base_url:
        raise ValueError("LLM_BASE_URL is not configured")

    title, truncated_text = _build_llm_content(html)

    system_prompt = (
        "Extract recipe from HTML text. Return ONLY valid JSON matching the schema. "
        'If invalid, return {"error":"invalid"}.'
    )
    user_prompt = (
        f"URL: {url}\nTitle: {title or 'Unknown'}\nContent:\n{truncated_text}\n\n"
        '{"title":string,"description":string|null,"source_url":string|null,"image_url":string|null,'
        '"tags":["string"],"servings":number|null,"estimated_time_minutes":number|null,'
        '"ingredients":[{"text":string,"quantity_display":string|null,"unit":string|null}],'
        '"steps":["string"],"notes":["string"]}\n\n'
        "Rules:\n"
        "- Separate ingredients: 'salt and pepper' = 2 entries\n"
        "- Extract units: '1 cup flour' → text:'flour', quantity_display:'1', unit:'cup'\n"
        "- Tags: general categories only (e.g., 'chicken', 'dinner'), not recipe names\n"
    )

    model_name = settings.llm_full_model_name or "full"

    payload = {
        "model": model_name,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 800,
        "stream": False,
    }

    if not settings.jarvis_auth_app_id or not settings.jarvis_auth_app_key:
        raise ValueError(
            "JARVIS_AUTH_APP_ID and JARVIS_AUTH_APP_KEY must be set for LLM proxy authentication"
        )

    headers = {
        "Content-Type": "application/json",
        "X-Jarvis-App-Id": settings.jarvis_auth_app_id,
        "X-Jarvis-App-Key": settings.jarvis_auth_app_key,
    }

    timeout = httpx.Timeout(90.0, read=80.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.llm_base_url}/v1/chat/completions", json=payload, headers=headers
        )
    response.raise_for_status()

    data = response.json()

    # Check for error response
    if isinstance(data, dict) and "error" in data:
        error_info = data["error"]
        error_type = error_info.get("type", "unknown_error")
        error_message = error_info.get("message", "Unknown error")
        logger.error(
            "LLM proxy returned error for url=%s: type=%s, message=%s",
            url,
            error_type,
            error_message[:500],
        )
        raise ValueError(f"LLM proxy error ({error_type}): {error_message}")

    content = None
    if isinstance(data, dict):
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                message = choice.get("message") or {}
                content = message.get("content")
        if not content and "message" in data:
            message = data.get("message") or {}
            content = message.get("content")
        if not content and "content" in data:
            content = data.get("content")

    if not content or not isinstance(content, str):
        raise ValueError("LLM response missing assistant content")

    if content.strip() and not (
        content.strip().startswith("{") or content.strip().startswith("[")
    ):
        logger.warning(
            "LLM response with json_object format doesn't start with { or [: url=%s, content_preview=%s",
            url,
            content[:200],
        )

    # Write debug log
    def _write_llm_debug(raw: str) -> None:
        try:
            safe = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()
            path = f"/tmp/llm_raw_{safe}.log"
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"url: {url}\n\n")
                f.write(raw)
            logger.info("LLM raw content saved to %s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to write LLM raw debug: %s", exc)

    _write_llm_debug(content)
    logger.info("LLM raw content (truncated) for url=%s: %s", url, content[:1000])

    try:
        parsed_json = _parse_llm_json_content(content)
    except ValueError:
        logger.error(
            "LLM response parse failed for url=%s; raw content (truncated): %s",
            url,
            content[:2000],
        )
        parsed_json = None
        repaired = _try_local_json_repair(content)
        if repaired:
            try:
                parsed_json = json.loads(repaired)
            except json.JSONDecodeError:
                parsed_json = None
        if parsed_json is None:
            schema_hint = (
                '{ "title": string, "description": string|null, "source_url": string|null, '
                '"image_url": string|null, "tags": [string], "servings": number|null, '
                '"estimated_time_minutes": number|null, "ingredients": ['
                '{"text": string, "quantity_display": string|null, "unit": string|null}], '
                '"steps": [string], "notes": [string] }'
            )
            repaired_llm = await _repair_json_via_full_llm(content, schema_hint)
            if repaired_llm:
                try:
                    parsed_json = json.loads(repaired_llm)
                except json.JSONDecodeError:
                    parsed_json = None
        if parsed_json is None:
            raise ValueError("LLM response was not valid JSON after repair attempts")

    # Normalize nullable collections
    if parsed_json.get("notes") is None:
        parsed_json["notes"] = []
    parsed_recipe = ParsedRecipe(**parsed_json)
    if not parsed_recipe.source_url:
        parsed_recipe.source_url = url
    return parsed_recipe
