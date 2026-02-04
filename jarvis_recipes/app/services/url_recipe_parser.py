import json
import os
import ipaddress
import logging
import re
import hashlib
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db.models import SourceType
from jarvis_recipes.app.schemas.recipe import IngredientCreate, RecipeCreate, StepCreate
from jarvis_recipes.app.services.llm_client import _repair_json_via_full_llm, _try_local_json_repair

logger = logging.getLogger(__name__)


class ParsedIngredient(BaseModel):
    text: str
    quantity_display: Optional[str] = None
    unit: Optional[str] = None


class ParsedRecipe(BaseModel):
    title: str
    description: Optional[str] = None
    source_url: Optional[str] = None
    image_url: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    servings: Optional[int] = None
    estimated_time_minutes: Optional[int] = None
    ingredients: List[ParsedIngredient] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class ParseResult(BaseModel):
    success: bool
    recipe: Optional[ParsedRecipe] = None
    used_llm: bool = False
    parser_strategy: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    next_action: Optional[str] = None
    next_action_reason: Optional[str] = None


class PreflightResult(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    next_action: Optional[str] = None
    next_action_reason: Optional[str] = None


def _is_private_host(host: str) -> bool:
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
    if not parsed.netloc or _is_private_host(parsed.hostname or ""):
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
        # Check if it's a blocking error that should trigger webview
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
    # This is still fast (we only read first ~5KB) but catches encoding issues early
    # HEAD requests don't have content, so we need to do a GET to sample
    if resp.status_code == 200:
        try:
            # Do a limited GET to sample the content (HEAD doesn't return body)
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                sample_resp = await client.get(
                    url, 
                    headers=headers, 
                    cookies=cookies,
                )
                # Only read first 5KB to keep it fast
                content_bytes = sample_resp.content[:5000]
            
            # Try to decode and validate encoding
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
                # Try UTF-8 as fallback
                try:
                    text_sample = content_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    # Encoding failed - suggest webview
                    return PreflightResult(
                        ok=False,
                        status_code=resp.status_code,
                        content_type=ctype,
                        error_code="encoding_error",
                        error_message="Unable to decode HTML content with detected encoding",
                        next_action="webview_extract",
                        next_action_reason="encoding_error",
                    )
            
            # Validate the decoded text looks like HTML
            if len(text_sample) > 100:
                has_html_tags = bool(re.search(r'<[a-z]+[^>]*>', text_sample[:2000], re.I))
                printable_count = sum(1 for c in text_sample[:2000] if (32 <= ord(c) <= 126) or c.isspace())
                printable_ratio = printable_count / min(len(text_sample[:2000]), 2000) if text_sample[:2000] else 0
                control_chars = sum(1 for c in text_sample[:2000] if ord(c) < 32 and c not in '\n\r\t')
                control_ratio = control_chars / min(len(text_sample[:2000]), 2000) if text_sample[:2000] else 0
                
                # If it doesn't look like valid HTML, suggest webview
                # Use same thresholds as fetch_html validation for consistency
                if not has_html_tags or printable_ratio < 0.6 or control_ratio > 0.1:
                    logger.warning(
                        "Preflight detected encoding/corruption issue for %s: has_tags=%s, printable=%.2f, control=%.2f",
                        url, has_html_tags, printable_ratio, control_ratio
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
            # If encoding check fails for any reason, log but don't fail preflight
            # (we'll catch it during actual processing)
            logger.warning("Preflight encoding check failed for %s: %s", url, exc)

    return PreflightResult(ok=True, status_code=resp.status_code, content_type=ctype)


def _parse_iso8601_duration(duration: str) -> Optional[int]:
    """
    Parse a minimal ISO-8601 duration string (e.g., PT1H30M) into minutes.
    """
    if not duration:
        return None
    match = re.match(r"PT?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total_minutes = hours * 60 + minutes + (1 if seconds >= 30 else 0)
    return total_minutes or None


def _parse_minutes(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        iso_minutes = _parse_iso8601_duration(value)
        if iso_minutes is not None:
            return iso_minutes
        match = re.search(r"(\d+)\s*(min|minute|minutes)", value, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def _parse_servings(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def _parse_servings_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    patterns = [
        r"serves\s+(\d+)",
        r"serve[s]?:\s*(\d+)",
        r"yield[s]?:\s*(\d+)",
    ]
    lowered = text.lower()
    for pat in patterns:
        m = re.search(pat, lowered, flags=re.I)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                continue
    return None


def _clean_parsed_ingredients(items: List[ParsedIngredient]) -> List[ParsedIngredient]:
    """Normalize ingredients: pull quantity/unit out of text if embedded; normalize fractions."""
    out: List[ParsedIngredient] = []
    fraction_chars = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
    quantity_unit_re = re.compile(rf"^\s*([\d\s\/\.\-+{fraction_chars}]+)\s+([A-Za-z][A-Za-z\.]*)\s+(.*)$")
    quantity_only_re = re.compile(rf"^\s*([\d\s\/\.\-+{fraction_chars}]+)\s+(.*)$")

    def split_qty_tokens(qty: str) -> tuple[Optional[str], Optional[str]]:
        """Split a qty string like '1 pound' into ('1', 'pound') if unit recognized."""
        if not qty:
            return None, None
        tokens = qty.split()
        numeric_tokens = []
        unit_token = None
        for tok in tokens:
            norm = _normalize_unit_token(tok)
            if _is_known_unit(tok):
                unit_token = tok
                break
            numeric_tokens.append(tok)
        if numeric_tokens:
            qd = _normalize_fraction_display(" ".join(numeric_tokens))
        else:
            qd = None
        return qd, unit_token

    def split_from_text(text: str) -> tuple[Optional[str], Optional[str], str]:
        raw = _clean_text(text)
        if not raw:
            return None, None, raw
        m = quantity_unit_re.match(raw)
        if m:
            qd = _normalize_fraction_display(_clean_text(m.group(1)))
            unit = _clean_text(m.group(2))
            name = _clean_text(m.group(3))
            if _is_known_unit(unit):
                return qd, unit, name
        m = quantity_only_re.match(raw)
        if m:
            qd = _normalize_fraction_display(_clean_text(m.group(1)))
            name = _clean_text(m.group(2))
            return qd, None, name
        return None, None, raw

    for ing in items:
        qty = _normalize_fraction_display(ing.quantity_display)
        unit = _clean_text(ing.unit) if ing.unit else None
        name = _clean_text(ing.text)

        # If name starts with quantity/unit, extract.
        qd2, unit2, name2 = split_from_text(name)
        if qd2 and not qty:
            qty = qd2
        if unit2 and not unit:
            unit = unit2
        name = name2 or name

        # If quantity_display itself includes a unit token, split it.
        if qty and not unit:
            qd_split, unit_split = split_qty_tokens(qty)
            if unit_split and _is_known_unit(unit_split):
                unit = unit_split
            if qd_split:
                qty = qd_split

        # If qty still includes the unit word, strip it.
        if qty and unit:
            unit_norm = _normalize_unit_token(unit)
            tokens = [t for t in qty.split() if _normalize_unit_token(t) != unit_norm]
            qty = _normalize_fraction_display(" ".join(tokens)) or qty

        out.append(ParsedIngredient(text=name, quantity_display=qty, unit=unit))
    return out


def _coerce_keywords(value, recipe_title: Optional[str] = None) -> List[str]:
    """
    Extract and filter tags from keywords, removing recipe-specific tags
    and keeping only general categories useful for filtering.
    """
    if not value:
        return []
    
    # Extract all keywords
    raw_tags = []
    if isinstance(value, str):
        raw_tags = [kw.strip() for kw in value.split(",") if kw.strip()]
    elif isinstance(value, Sequence):
        for item in value:
            if isinstance(item, str):
                raw_tags.extend([kw.strip() for kw in item.split(",") if kw.strip()])
    
    if not raw_tags:
        return []
    
    # Normalize recipe title for comparison (lowercase, remove common words)
    title_words = set()
    if recipe_title:
        title_normalized = recipe_title.lower()
        # Remove common recipe words
        for word in ["recipe", "recipes", "how to", "how to make", "easy", "best", "homemade"]:
            title_normalized = title_normalized.replace(word, "")
        title_words = set(title_normalized.split())
        # Filter out very short words
        title_words = {w for w in title_words if len(w) > 3}
    
    # Filter tags: keep only general categories
    filtered_tags = []
    for tag in raw_tags:
        tag_lower = tag.lower().strip()
        
        # Skip empty tags
        if not tag_lower:
            continue
        
        # Skip tags that are too similar to the recipe title
        if title_words:
            tag_words = set(tag_lower.split())
            # If tag contains 2+ words from the title, it's probably too specific
            overlap = len(tag_words & title_words)
            if overlap >= 2:
                continue
            # Skip if tag is essentially the recipe name with minor variations
            if tag_lower in recipe_title.lower() or recipe_title.lower() in tag_lower:
                continue
        
        # Skip tags that are just recipe name variations
        # (e.g., "turkey pot pie", "easy turkey pot pie", "turkey pot pie recipe")
        if recipe_title and len(tag_lower.split()) >= 3:
            # If tag has 3+ words and contains the recipe name, skip it
            if any(word in tag_lower for word in title_words if len(word) > 4):
                continue
        
        # Keep general categories (single words or common categories)
        # Examples: "chicken", "dessert", "vegetarian", "gluten-free", "dinner", "breakfast"
        if len(tag_lower.split()) <= 2:  # Keep 1-2 word tags (more likely to be categories)
            filtered_tags.append(tag)
        elif any(category in tag_lower for category in [
            "free", "friendly", "diet", "cuisine", "course", "meal", "type",
            "vegetarian", "vegan", "gluten", "dairy", "nut", "paleo", "keto",
            "breakfast", "lunch", "dinner", "dessert", "appetizer", "snack",
            "american", "italian", "mexican", "asian", "french", "indian", "chinese",
            "quick", "slow", "cooker", "instant", "one-pot", "sheet-pan"
        ]):
            # Keep tags that contain common category keywords
            filtered_tags.append(tag)
    
    # Deduplicate (case-insensitive)
    seen = set()
    unique_tags = []
    for tag in filtered_tags:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            unique_tags.append(tag)
    
    return unique_tags


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_image(value) -> Optional[str]:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        for item in value:
            if isinstance(item, str):
                return item
    return None


def _extract_instruction_text(instructions) -> List[str]:
    steps: List[str] = []
    if isinstance(instructions, list):
        for entry in instructions:
            if isinstance(entry, str):
                cleaned = _clean_text(entry)
                if cleaned:
                    steps.append(cleaned)
            elif isinstance(entry, dict):
                text_val = entry.get("text") or entry.get("description")
                cleaned = _clean_text(text_val or "")
                if cleaned:
                    steps.append(cleaned)
    elif isinstance(instructions, str):
        cleaned = _clean_text(instructions)
        if cleaned:
            steps.append(cleaned)
    return steps


COMMON_UNITS = {
    "tsp",
    "teaspoon",
    "tbsp",
    "tablespoon",
    "c",
    "cup",
    "oz",
    "ounce",
    "fl",
    "fl-oz",
    "pint",
    "pt",
    "quart",
    "qt",
    "gallon",
    "gal",
    "g",
    "gram",
    "kg",
    "lb",
    "lbs",
    "pound",
    "ml",
    "l",
    "liter",
    "litre",
    "stick",
    "clove",
    "slice",
    "can",
    "package",
    "pkg",
    "packet",
    "bunch",
    "head",
    "ear",
    "piece",
}


def _normalize_unit_token(unit: str) -> str:
    token = unit.lower().strip(".")
    if token.endswith("s"):
        token = token[:-1]
    return token


def _is_known_unit(unit: str) -> bool:
    return _normalize_unit_token(unit) in COMMON_UNITS


FRACTION_MAP = {
    "¼": "1/4",
    "½": "1/2",
    "¾": "3/4",
    "⅐": "1/7",
    "⅑": "1/9",
    "⅒": "1/10",
    "⅓": "1/3",
    "⅔": "2/3",
    "⅕": "1/5",
    "⅖": "2/5",
    "⅗": "3/5",
    "⅘": "4/5",
    "⅙": "1/6",
    "⅚": "5/6",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}


def _normalize_fraction_display(qty: Optional[str]) -> Optional[str]:
    if not qty:
        return qty
    s = qty
    # Ensure a space before a unicode fraction when attached to a digit, e.g., "1½" -> "1 ½"
    fraction_chars = "".join(FRACTION_MAP.keys())
    s = re.sub(rf"(\d)([{fraction_chars}])", r"\1 \2", s)
    for k, v in FRACTION_MAP.items():
        s = s.replace(k, v)
    s = re.sub(r"\s+", " ", s).strip()
    # Normalize pure numeric strings like "02" to "2"
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        try:
            num = float(s)
            if num.is_integer():
                s = str(int(num))
            else:
                s = str(num)
        except ValueError:
            pass
    return s or None


def _extract_ingredients(ingredients) -> List[ParsedIngredient]:
    parsed: List[ParsedIngredient] = []
    fraction_chars = "¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞"
    quantity_unit_re = re.compile(rf"^\s*([\d\s\/\.\-+{fraction_chars}]+)\s+([A-Za-z][A-Za-z\.]*)\s+(.*)$")
    quantity_only_re = re.compile(rf"^\s*([\d\s\/\.\-+{fraction_chars}]+)\s+(.*)$")
    paren_cleanup_re = re.compile(r"\s*\([^)]*\)\s*")

    def clean_name(text: str) -> str:
        cleaned = _clean_text(text)
        # Remove parentheses and their content (handles both normal and malformed cases)
        # First, remove properly matched parentheses: (content)
        cleaned = paren_cleanup_re.sub(" ", cleaned)
        # Then, remove any remaining unmatched closing parentheses
        cleaned = re.sub(r"\s*\)\s*", " ", cleaned)
        # Remove any remaining unmatched opening parentheses
        cleaned = re.sub(r"\s*\(\s*", " ", cleaned)
        # Clean up any double spaces and trim
        cleaned = _clean_text(cleaned)
        # Remove trailing spaces/parentheses that might have been left behind
        cleaned = cleaned.rstrip(" )")
        if cleaned.lower().startswith("recipe "):
            cleaned = cleaned[7:]
        return cleaned

    def split_line(line: str) -> ParsedIngredient:
        raw = _clean_text(line)
        if not raw:
            return ParsedIngredient(text=raw)

        # Try qty + unit + name
        m = quantity_unit_re.match(raw)
        if m:
            qd = _normalize_fraction_display(_clean_text(m.group(1)))
            unit = _clean_text(m.group(2))
            name = clean_name(m.group(3))
            if _is_known_unit(unit):
                return ParsedIngredient(text=name, quantity_display=qd, unit=unit)
            # If the token isn't a recognized unit, fall back to qty + name

        # Try qty + name (no unit)
        m = quantity_only_re.match(raw)
        if m:
            qd = _normalize_fraction_display(_clean_text(m.group(1)))
            name = clean_name(m.group(2))
            return ParsedIngredient(text=name, quantity_display=qd, unit=None)

        return ParsedIngredient(text=clean_name(raw))

    if isinstance(ingredients, list):
        logger.debug("Extracting ingredients from list of %d items", len(ingredients))
        for idx, raw in enumerate(ingredients):
            if isinstance(raw, str):
                cleaned = _clean_text(raw)
                if cleaned:
                    ingredient = split_line(cleaned)
                    parsed.append(ingredient)
                    logger.debug("Ingredient %d: '%s' -> text='%s', qty='%s', unit='%s'", 
                               idx, raw[:50], ingredient.text[:30], ingredient.quantity_display, ingredient.unit)
                else:
                    logger.debug("Ingredient %d: string was empty after cleaning", idx)
            elif isinstance(raw, dict):
                text_val = raw.get("text") or raw.get("name")
                if text_val:
                    quantity = _clean_text(raw.get("amount") or raw.get("quantity") or "")
                    unit = _clean_text(raw.get("unit") or "")
                    # If dict still has combined text, try to split; otherwise use provided fields.
                    if not quantity and not unit:
                        ingredient = split_line(text_val)
                        parsed.append(ingredient)
                    else:
                        ingredient = ParsedIngredient(
                            text=_clean_text(text_val),
                            quantity_display=quantity or None,
                            unit=unit or None,
                        )
                        parsed.append(ingredient)
                    logger.debug("Ingredient %d (dict): text='%s', qty='%s', unit='%s'", 
                               idx, ingredient.text[:30], ingredient.quantity_display, ingredient.unit)
                else:
                    logger.debug("Ingredient %d: dict had no text/name field", idx)
            else:
                logger.debug("Ingredient %d: unexpected type %s", idx, type(raw).__name__)
    elif isinstance(ingredients, str):
        # Single string ingredient
        cleaned = _clean_text(ingredients)
        if cleaned:
            parsed.append(split_line(cleaned))
    else:
        logger.warning("Ingredients input is not a list or string: %s", type(ingredients).__name__)
    
    logger.info("Extracted %d ingredients from input", len(parsed))
    return parsed


def _parse_llm_json_content(raw: str) -> dict:
    """
    Parse LLM content into JSON.
    If the model echoed prompt text, attempt to extract the first JSON object.
    """
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


async def fetch_html(url: str) -> str:
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("Invalid URL")
    if _is_private_host(parsed_url.hostname or ""):
        raise ValueError("URL points to a private or disallowed host")

    headers = {
        # Some sites block obvious bots; use a common browser UA to reduce 403s.
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
    # Slightly higher timeouts; some sites are slow to respond.
    timeout = httpx.Timeout(15.0, read=15.0, connect=5.0)

    async def _try_fetch(target_url: str, extra_headers: Optional[dict] = None) -> httpx.Response:
        merged_headers = headers | (extra_headers or {})
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=merged_headers) as client:
            return await client.get(target_url)

    try:
        response = await _try_fetch(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # Retry with slightly different headers; if still forbidden, attempt a text proxy fallback.
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
        # Network-level or timeout: fall back to jina proxy to avoid hard fail on slow sites.
        proxy_url = f"https://r.jina.ai/{url}"
        response = await _try_fetch(proxy_url, {"Accept": "text/plain"})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    # The proxy returns text/plain; allow it.
    if "text/html" not in content_type and "text/plain" not in content_type:
        raise ValueError(f"Unsupported content type: {content_type}")
    
    # Explicitly handle encoding to avoid garbled text
    # httpx should handle decompression automatically, but we need to ensure proper encoding
    try:
        # Get the raw bytes first
        content_bytes = response.content
        
        # Try to detect encoding from Content-Type header
        encoding = None
        if "charset=" in content_type.lower():
            try:
                encoding = content_type.split("charset=")[1].split(";")[0].strip().strip('"\'')
            except (IndexError, AttributeError):
                pass
        
        # If no encoding in header, try UTF-8 first (most common for modern websites)
        if not encoding:
            encoding = "utf-8"
        
        # Decode with detected/default encoding
        try:
            text = content_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            # If that fails, try to detect from HTML meta tag
            try:
                # Try UTF-8 first as fallback
                text = content_bytes.decode("utf-8", errors="replace")
                # Look for charset in HTML
                encoding_match = re.search(r'<meta[^>]+charset=["\']?([^"\'>\s]+)', text, re.I)
                if encoding_match:
                    detected_encoding = encoding_match.group(1).lower()
                    if detected_encoding and detected_encoding != "utf-8":
                        try:
                            text = content_bytes.decode(detected_encoding)
                        except (UnicodeDecodeError, LookupError):
                            # Keep the UTF-8 with replacement chars version
                            pass
            except (UnicodeDecodeError, LookupError):
                # Last resort: use response.text which should handle it
                text = response.text
        
        # Validate that we got reasonable text (not binary garbage)
        if text and len(text) > 100:
            # Better validation: check for actual HTML structure, not just characters
            # Look for common HTML tags and reasonable text content
            has_html_tags = bool(re.search(r'<[a-z]+[^>]*>', text[:2000], re.I))
            # Check for reasonable ratio of printable ASCII/Latin characters
            sample = text[:2000]
            printable_count = sum(1 for c in sample if (32 <= ord(c) <= 126) or c.isspace())
            printable_ratio = printable_count / len(sample) if sample else 0
            
            # Check for excessive control characters or binary-looking sequences
            control_chars = sum(1 for c in sample if ord(c) < 32 and c not in '\n\r\t')
            control_ratio = control_chars / len(sample) if sample else 0
            
            # Valid HTML should have tags and mostly printable characters
            if has_html_tags and printable_ratio > 0.6 and control_ratio < 0.1:
                return text
            else:
                logger.warning(
                    "HTML validation failed for %s: has_tags=%s, printable_ratio=%.2f, control_ratio=%.2f",
                    url, has_html_tags, printable_ratio, control_ratio
                )
                # Raise a specific exception that can trigger webview fallback
                raise ValueError("HTML content appears corrupted or invalid encoding")
        
        # If validation failed, try response.text as fallback
        text_fallback = response.text
        # Validate fallback too
        if text_fallback and len(text_fallback) > 100:
            has_html_tags = bool(re.search(r'<[a-z]+[^>]*>', text_fallback[:2000], re.I))
            if has_html_tags:
                return text_fallback
        
        raise ValueError("Unable to decode HTML content with valid encoding")
    except ValueError:
        # Re-raise ValueError (our validation errors)
        raise
    except Exception as exc:
        logger.warning("Encoding error when fetching %s: %s. Attempting fallback.", url, exc)
        # Last resort: try response.text
        try:
            text_fallback = response.text
            if text_fallback and len(text_fallback) > 100:
                has_html_tags = bool(re.search(r'<[a-z]+[^>]*>', text_fallback[:2000], re.I))
                if has_html_tags:
                    return text_fallback
        except (UnicodeDecodeError, AttributeError):
            pass
        raise ValueError(f"HTML content encoding error: {exc}")


def extract_recipe_from_schema_org(html: str, url: str) -> Optional[ParsedRecipe]:
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    logger.info("Found %d JSON-LD script blocks", len(scripts))
    
    for idx, script in enumerate(scripts):
        raw_json = script.string or script.get_text()
        if not raw_json:
            logger.debug("JSON-LD block %d is empty", idx)
            continue
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.warning("JSON-LD block %d failed to parse: %s (first 200 chars: %s)", idx, exc, raw_json[:200])
            continue

        candidates = []
        if isinstance(data, dict) and "@graph" in data:
            graph = data.get("@graph") or []
            if isinstance(graph, list):
                candidates.extend(graph)
                logger.info("Found @graph with %d items", len(graph))
        if isinstance(data, list):
            candidates.extend(data)
            logger.info("JSON-LD is a list with %d items", len(data))
        elif isinstance(data, dict):
            candidates.append(data)
            logger.info("JSON-LD is a single object")

        for obj_idx, obj in enumerate(candidates):
            if not isinstance(obj, dict):
                continue
            obj_type = obj.get("@type")
            if not obj_type:
                logger.debug("Candidate %d has no @type", obj_idx)
                continue
            types = [obj_type] if isinstance(obj_type, str) else obj_type
            type_str = ", ".join(str(t) for t in types)
            logger.info("Candidate %d has @type: %s", obj_idx, type_str)
            
            if not any(str(t).lower() == "recipe" for t in types):
                logger.debug("Candidate %d is not a Recipe (type: %s), skipping", obj_idx, type_str)
                continue

            title = _clean_text(obj.get("name") or "")
            recipe_ingredient_raw = obj.get("recipeIngredient") or []
            recipe_instructions_raw = obj.get("recipeInstructions") or []
            
            logger.debug("Recipe candidate %d raw data: recipeIngredient type=%s, len=%s, recipeInstructions type=%s, len=%s",
                        obj_idx, type(recipe_ingredient_raw).__name__, 
                        len(recipe_ingredient_raw) if isinstance(recipe_ingredient_raw, (list, str)) else "N/A",
                        type(recipe_instructions_raw).__name__,
                        len(recipe_instructions_raw) if isinstance(recipe_instructions_raw, (list, str)) else "N/A")
            
            ingredients = _extract_ingredients(recipe_ingredient_raw)
            steps = _extract_instruction_text(recipe_instructions_raw)
            
            # Handle None values from extraction functions
            ingredients = ingredients or []
            steps = steps or []
            
            logger.info("Recipe candidate %d: title=%s, ingredients=%d, steps=%d", 
                       obj_idx, title[:50] if title else "None", len(ingredients), len(steps))
            
            if not title:
                logger.warning("Recipe candidate %d missing title", obj_idx)
                continue
            if not ingredients:
                logger.warning("Recipe candidate %d missing ingredients (raw had %d items)", 
                             obj_idx, len(recipe_ingredient_raw) if isinstance(recipe_ingredient_raw, list) else 1 if recipe_ingredient_raw else 0)
                # Log first ingredient to see what we're getting
                if recipe_ingredient_raw and isinstance(recipe_ingredient_raw, list) and len(recipe_ingredient_raw) > 0:
                    logger.warning("First ingredient raw: %s", str(recipe_ingredient_raw[0])[:200])
                continue
            if not steps:
                logger.warning("Recipe candidate %d missing steps (raw had %d items)", 
                             obj_idx, len(recipe_instructions_raw) if isinstance(recipe_instructions_raw, list) else 1 if recipe_instructions_raw else 0)
                continue

            # Extract tags, filtering out recipe-specific ones
            keywords = obj.get("keywords")
            recipe_category = obj.get("recipeCategory") or []
            recipe_cuisine = obj.get("recipeCuisine") or []
            
            # Combine keywords with category and cuisine
            all_keywords = []
            if keywords:
                all_keywords.append(keywords)
            if recipe_category:
                if isinstance(recipe_category, list):
                    all_keywords.extend(recipe_category)
                else:
                    all_keywords.append(recipe_category)
            if recipe_cuisine:
                if isinstance(recipe_cuisine, list):
                    all_keywords.extend(recipe_cuisine)
                else:
                    all_keywords.append(recipe_cuisine)
            
            tags = _coerce_keywords(all_keywords if all_keywords else None, recipe_title=title)
            
            parsed = ParsedRecipe(
                title=title,
                description=_clean_text(obj.get("description") or ""),
                source_url=url,
                image_url=_extract_image(obj.get("image")),
                tags=tags,
                servings=_parse_servings(obj.get("recipeYield")),
                estimated_time_minutes=_parse_minutes(obj.get("totalTime")),
                ingredients=ingredients,
                steps=steps,
            )
            return parsed
    return None


def extract_recipe_from_microdata(html: str, url: str) -> Optional[ParsedRecipe]:
    """
    Placeholder for microdata/RDFa parsing. Implement as needed.
    """
    return None


def extract_recipe_heuristic(html: str, url: str) -> Optional[ParsedRecipe]:
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("h1") or soup.title
    title = _clean_text(title_tag.get_text()) if title_tag else None

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.find(class_=re.compile("recipe|post|content", re.I))
        or soup.body
    )
    if not container:
        return None

    ingredient_candidates = container.find_all(["ul", "ol"])
    best_ingredients: List[str] = []
    for lst in ingredient_candidates:
        items = [li.get_text(" ", strip=True) for li in lst.find_all("li")]
        if len(items) < 2:
            continue
        matches = sum(
            1
            for item in items
            if re.search(r"\d|\b(cup|tsp|tbsp|tablespoon|teaspoon|ounce|gram|kg|ml|l)\b", item, flags=re.I)
        )
        if matches >= max(2, len(items) // 2):
            best_ingredients = items
            break
    ingredients = [ParsedIngredient(text=_clean_text(i)) for i in best_ingredients if _clean_text(i)]

    instruction_heading = container.find(string=re.compile("direction|instruction|method", re.I))
    steps: List[str] = []
    if instruction_heading and instruction_heading.parent:
        sibling = instruction_heading.parent.find_next_sibling(["ol", "ul", "p", "div"])
        if sibling:
            if sibling.name in {"ol", "ul"}:
                steps = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
            else:
                steps = [p.get_text(" ", strip=True) for p in sibling.find_all("p")] or [sibling.get_text(" ", strip=True)]
    if not steps:
        ordered_lists = container.find_all("ol")
        if ordered_lists:
            steps = [li.get_text(" ", strip=True) for li in ordered_lists[0].find_all("li")]

    steps = [_clean_text(s) for s in steps if _clean_text(s)]
    servings = _parse_servings_from_text(container.get_text(" ", strip=True))

    if title and ingredients and steps:
        return ParsedRecipe(title=title, source_url=url, ingredients=ingredients, steps=steps, servings=servings)
    return None


def clean_soup_for_content(soup: BeautifulSoup) -> None:
    """Remove obvious boilerplate nodes before extracting candidate content."""
    for noisy in soup.find_all(["header", "footer", "nav", "aside", "form"]):
        noisy.decompose()
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()


def find_main_node(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Find the main content node in the soup, prioritizing recipe-specific containers."""
    return (
        soup.find(attrs={"itemtype": re.compile("Recipe", re.I)})  # recipe microdata container if present
        or soup.find("article")
        or soup.find("main")
        or soup.body
    )


def _find_ingredient_items(container) -> List[str]:
    candidates = container.find_all(["ul", "ol"])
    best_items: List[str] = []
    best_score = -1
    for lst in candidates:
        items = [li.get_text(" ", strip=True) for li in lst.find_all("li")]
        if len(items) < 2:
            continue
        matches = sum(
            1
            for item in items
            if re.search(r"\d|\b(cup|tsp|tbsp|tablespoon|teaspoon|ounce|oz|gram|kg|ml|l)\b", item, flags=re.I)
        )
        score = matches * 2 + len(items)
        if score > best_score:
            best_score = score
            best_items = items
    return [_clean_text(i) for i in best_items if _clean_text(i)]


def _find_instruction_items(container) -> List[str]:
    steps: List[str] = []
    
    # Strategy 1: Look for ordered lists (most common for recipe steps)
    ordered_lists = container.find_all("ol")
    if ordered_lists:
        # Find the best ordered list (usually the longest one with recipe-like content)
        best_list = None
        best_score = 0
        for ol in ordered_lists:
            items = [li.get_text(" ", strip=True) for li in ol.find_all("li")]
            # Score based on length and presence of action verbs
            score = len(items)
            action_verbs = sum(1 for item in items if re.search(r'\b(cook|bake|add|mix|stir|heat|pour|season|chop|slice|dice|mince|preheat)\b', item, re.I))
            score += action_verbs * 2
            if score > best_score and len(items) >= 3:
                best_score = score
                best_list = ol
        if best_list:
            steps = [li.get_text(" ", strip=True) for li in best_list.find_all("li")]
            return [_clean_text(s) for s in steps if _clean_text(s)]
    
    # Strategy 2: Look for headings like "How to make", "Instructions", "Directions", "Method"
    instruction_patterns = [
        r"how\s+to\s+make",
        r"instructions?",
        r"directions?",
        r"method",
        r"steps?",
        r"preparation",
    ]
    
    for pattern in instruction_patterns:
        heading = container.find(string=re.compile(pattern, re.I))
        if heading and heading.parent:
            # Look for next sibling that contains steps
            sibling = heading.parent.find_next_sibling(["ol", "ul", "div", "section"])
            if sibling:
                if sibling.name in {"ol", "ul"}:
                    steps = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
                else:
                    # Look for paragraphs or list items within the sibling
                    paragraphs = sibling.find_all("p")
                    if paragraphs:
                        steps = [p.get_text(" ", strip=True) for p in paragraphs]
                    else:
                        list_items = sibling.find_all("li")
                        if list_items:
                            steps = [li.get_text(" ", strip=True) for li in list_items]
                        else:
                            # Try to extract structured content (headings with following text)
                            headings = sibling.find_all(["h2", "h3", "h4", "strong", "b"])
                            for h in headings:
                                step_text = h.get_text(" ", strip=True)
                                # Get following text until next heading or end
                                next_elem = h.find_next_sibling()
                                if next_elem and next_elem.name not in ["h2", "h3", "h4", "strong", "b"]:
                                    step_text += " " + next_elem.get_text(" ", strip=True)
                                if step_text and len(step_text) > 10:
                                    steps.append(step_text)
            if steps:
                break
    
    # Strategy 3: Look for structured recipe steps (headings like "Step 1:", "1.", etc.)
    if not steps:
        # Find all headings that look like step headers
        step_headings = container.find_all(string=re.compile(r'^(step\s+\d+|cook|bake|make|prep|prepare|season|add|mix|stir|heat|pour)', re.I))
        for heading_text in step_headings:
            parent = heading_text.parent
            if parent:
                # Get the text content following this heading
                step_content = parent.get_text(" ", strip=True)
                # Also try to get next sibling content
                next_sib = parent.find_next_sibling()
                if next_sib:
                    step_content += " " + next_sib.get_text(" ", strip=True)
                if step_content and len(step_content) > 20:
                    steps.append(step_content)
    
    # Strategy 4: Fallback - look for any ordered list
    if not steps:
        ordered_lists = container.find_all("ol")
        if ordered_lists:
            # Use the longest ordered list
            longest = max(ordered_lists, key=lambda ol: len(ol.find_all("li")))
            steps = [li.get_text(" ", strip=True) for li in longest.find_all("li")]
    
    return [_clean_text(s) for s in steps if _clean_text(s)]


def _build_llm_content(html: str) -> Tuple[Optional[str], str]:
    # Safety check: detect if HTML is corrupted before processing
    if html and len(html) > 100:
        sample = html[:2000]
        printable_count = sum(1 for c in sample if (32 <= ord(c) <= 126) or c.isspace())
        printable_ratio = printable_count / len(sample) if sample else 0
        control_chars = sum(1 for c in sample if ord(c) < 32 and c not in '\n\r\t')
        control_ratio = control_chars / len(sample) if sample else 0
        
        # If too many control characters or too few printable chars, HTML is likely corrupted
        if printable_ratio < 0.5 or control_ratio > 0.15:
            logger.warning("Detected corrupted HTML in _build_llm_content: printable_ratio=%.2f, control_ratio=%.2f", printable_ratio, control_ratio)
            raise ValueError("HTML content appears corrupted - encoding error detected")
    
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("h1") or soup.title
    title = _clean_text(title_tag.get_text()) if title_tag else None
    # Capture JSON-LD scripts before cleaning/removal
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
        # Include all ingredients (they're usually not that many)
        parts.append("Ingredients:\n" + "\n".join(ingredients))
    if instructions:
        # Include all instructions - don't truncate steps
        # Steps are critical for recipe parsing
        parts.append("Instructions:\n" + "\n".join(instructions))

    # Fallback: include a larger slice of main text if parts are thin
    # This helps when structured extraction fails
    if len("\n\n".join(parts)) < 500:
        text = main_node.get_text("\n", strip=True)
        text = re.sub(r"\n{2,}", "\n", text)
        lines = text.splitlines()
        # Include more lines for fallback (up to 200)
        parts.append("\n".join(lines[:200]))

    combined = (title + "\n" if title else "") + "\n\n".join([p for p in parts if p])
    # Increase limit to 10000 to ensure we capture full recipe steps
    # LLM can handle this size, and steps are critical
    combined = combined[:10000]
    return title, combined


async def extract_recipe_via_llm(html: str, url: str, metadata: Optional[dict] = None) -> ParsedRecipe:
    settings = get_settings()
    if not settings.llm_base_url:
        raise ValueError("LLM_BASE_URL is not configured")

    soup = BeautifulSoup(html, "lxml")
    title, truncated_text = _build_llm_content(html)

    system_prompt = (
        "Extract recipe from HTML text. Return ONLY valid JSON matching the schema. "
        "If invalid, return {\"error\":\"invalid\"}."
    )
    user_prompt = (
        f"URL: {url}\nTitle: {title or 'Unknown'}\nContent:\n{truncated_text}\n\n"
        "Schema: {\"title\":string,\"description\":string|null,\"source_url\":string|null,\"image_url\":string|null,"
        "\"tags\":[\"string\"],\"servings\":number|null,\"estimated_time_minutes\":number|null,"
        "\"ingredients\":[{\"text\":string,\"quantity_display\":string|null,\"unit\":string|null}],"
        "\"steps\":[\"string\"],\"notes\":[\"string\"]}\n\n"
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
        raise ValueError("JARVIS_AUTH_APP_ID and JARVIS_AUTH_APP_KEY must be set for LLM proxy authentication")

    headers = {
        "Content-Type": "application/json",
        "X-Jarvis-App-Id": settings.jarvis_auth_app_id,
        "X-Jarvis-App-Key": settings.jarvis_auth_app_key,
    }

    # Background-friendly: allow longer LLM response time.
    timeout = httpx.Timeout(90.0, read=80.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.llm_base_url}/v1/chat/completions", json=payload, headers=headers
        )
    response.raise_for_status()

    data = response.json()
    
    # Check for error response from LLM proxy (per PRD: json-response-format-support.md)
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
    
    # With response_format: {"type": "json_object"}, the proxy should return valid JSON
    # Log if content doesn't look like JSON (starts with { or [)
    if content.strip() and not (content.strip().startswith("{") or content.strip().startswith("[")):
        logger.warning(
            "LLM response with json_object format doesn't start with {{ or [: url=%s, content_preview=%s",
            url,
            content[:200],
        )

    # Persist full raw content to a tmp file for debugging malformed JSON.
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

    # Log raw content (truncated) for debugging schema/JSON issues.
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


def normalize_parsed_recipe(parsed: ParsedRecipe) -> RecipeCreate:
    ingredients = [
        IngredientCreate(
            text=item.text,
            quantity_display=_normalize_fraction_display(item.quantity_display),
            unit=item.unit,
        )
        for item in parsed.ingredients
    ]
    steps = [StepCreate(step_number=i + 1, text=text) for i, text in enumerate(parsed.steps)]

    return RecipeCreate(
        title=parsed.title,
        description=parsed.description,
        servings=parsed.servings,
        total_time_minutes=parsed.estimated_time_minutes,
        source_type=SourceType.URL,
        source_url=parsed.source_url,
        image_url=parsed.image_url,
        ingredients=ingredients,
        steps=steps,
        tags=_coerce_keywords(parsed.tags, recipe_title=parsed.title) if parsed.tags else [],
    )


async def parse_recipe_from_url(url: str, use_llm_fallback: bool = True) -> ParseResult:
    warnings: List[str] = []
    try:
        html = await fetch_html(url)
    except ValueError as exc:
        error_msg = str(exc)
        # Check if this is an encoding/corruption error (not just invalid URL)
        is_encoding_error = "encoding" in error_msg.lower() or "corrupted" in error_msg.lower() or "invalid encoding" in error_msg.lower()
        
        if is_encoding_error:
            logger.warning("Encoding/corruption error for %s: %s. Suggesting webview fallback.", url, error_msg)
            return ParseResult(
                success=False,
                error_code="fetch_failed",
                error_message=error_msg,
                warnings=warnings + ["encoding_error"],
                next_action="webview_extract",
                next_action_reason="encoding_error",
            )
        else:
            # Regular invalid URL error
            return ParseResult(
                success=False,
                error_code="invalid_url",
                error_message=error_msg,
                warnings=warnings,
            )
    except httpx.HTTPStatusError as exc:
        logger.exception("Failed to fetch URL %s (status=%s)", url, exc.response.status_code if exc.response else "unknown")
        return ParseResult(
            success=False,
            error_code="fetch_failed",
            error_message=f"status_{exc.response.status_code if exc.response else 'unknown'}",
            warnings=warnings + ["blocked_by_site" if exc.response and exc.response.status_code == 403 else "fetch_http_error"],
        )
    except httpx.HTTPError as exc:
        logger.exception("Failed to fetch URL %s", url)
        return ParseResult(
            success=False,
            error_code="fetch_failed",
            error_message=str(exc),
            warnings=warnings + ["fetch_http_error"],
        )

    parsed = extract_recipe_from_schema_org(html, url)
    if parsed:
        parsed.ingredients = _clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="schema_org_json_ld", warnings=warnings)

    parsed = extract_recipe_from_microdata(html, url)
    if parsed:
        parsed.ingredients = _clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="microdata", warnings=warnings)

    parsed = extract_recipe_heuristic(html, url)
    if parsed:
        parsed.ingredients = _clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(success=True, recipe=parsed, used_llm=False, parser_strategy="heuristic", warnings=warnings)

    if use_llm_fallback:
        try:
            parsed = await extract_recipe_via_llm(html, url, metadata={"length": len(html)})
            warnings.append("LLM fallback used; please verify ingredients.")
            parsed.ingredients = _clean_parsed_ingredients(parsed.ingredients)
            return ParseResult(success=True, recipe=parsed, used_llm=True, parser_strategy="llm_fallback", warnings=warnings)
        except ValueError as exc:
            # Encoding/corruption error detected in _build_llm_content
            error_msg = str(exc)
            if "corrupted" in error_msg.lower() or "encoding" in error_msg.lower():
                logger.warning("Encoding error detected in LLM path for %s: %s. Suggesting webview fallback.", url, error_msg)
                return ParseResult(
                    success=False,
                    error_code="fetch_failed",
                    error_message=error_msg,
                    warnings=warnings + ["encoding_error"],
                    next_action="webview_extract",
                    next_action_reason="encoding_error",
                )
            else:
                # Other ValueError, re-raise or handle as generic error
                raise
        except httpx.TimeoutException as exc:
            logger.exception("LLM fallback timeout for %s", url)
            return ParseResult(
                success=False,
                used_llm=True,
                parser_strategy="llm_fallback",
                error_code="llm_timeout",
                error_message=str(exc),
                warnings=warnings,
            )
        except json.JSONDecodeError as exc:
            logger.exception("LLM returned invalid JSON for %s", url)
            return ParseResult(
                success=False,
                used_llm=True,
                parser_strategy="llm_fallback",
                error_code="llm_failed",
                error_message="Invalid JSON from LLM",
                warnings=warnings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM fallback failed for %s", url)
            return ParseResult(
                success=False,
                used_llm=True,
                parser_strategy="llm_fallback",
                error_code="llm_failed",
                error_message=str(exc),
                warnings=warnings,
            )

    return ParseResult(success=False, error_code="parse_failed", error_message="Unable to parse recipe", warnings=warnings)

