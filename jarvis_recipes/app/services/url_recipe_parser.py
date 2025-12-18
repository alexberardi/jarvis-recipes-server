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
        except Exception:
            cookies = {}

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
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
        return PreflightResult(
            ok=False,
            status_code=resp.status_code,
            content_type=ctype,
            error_code="fetch_failed",
            error_message=f"Site returned status {resp.status_code}.",
        )
    if "text/html" not in ctype and "application/xhtml" not in ctype and ctype:
        return PreflightResult(
            ok=False,
            status_code=resp.status_code,
            content_type=ctype,
            error_code="unsupported_content_type",
            error_message=f"Unsupported content type: {ctype}",
        )

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


def _coerce_keywords(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [kw.strip() for kw in value.split(",") if kw.strip()]
    if isinstance(value, Sequence):
        tags = []
        for item in value:
            if isinstance(item, str):
                tags.extend([kw.strip() for kw in item.split(",") if kw.strip()])
        return tags
    return []


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
        cleaned = paren_cleanup_re.sub(" ", cleaned)
        cleaned = _clean_text(cleaned)
        if cleaned.lower().startswith("recipe "):
            cleaned = cleaned[7:]
        return cleaned


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
        for raw in ingredients:
            if isinstance(raw, str):
                cleaned = _clean_text(raw)
                if cleaned:
                    parsed.append(split_line(cleaned))
            elif isinstance(raw, dict):
                text_val = raw.get("text") or raw.get("name")
                if text_val:
                    quantity = _clean_text(raw.get("amount") or raw.get("quantity") or "")
                    unit = _clean_text(raw.get("unit") or "")
                    # If dict still has combined text, try to split; otherwise use provided fields.
                    if not quantity and not unit:
                        parsed.append(split_line(text_val))
                    else:
                        parsed.append(
                            ParsedIngredient(
                                text=_clean_text(text_val),
                                quantity_display=quantity or None,
                                unit=unit or None,
                            )
                        )
    return parsed


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
    return response.text


def extract_recipe_from_schema_org(html: str, url: str) -> Optional[ParsedRecipe]:
    soup = BeautifulSoup(html, "lxml")
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in scripts:
        raw_json = script.string or script.get_text()
        if not raw_json:
            continue
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        candidates = []
        if isinstance(data, dict) and "@graph" in data:
            graph = data.get("@graph") or []
            if isinstance(graph, list):
                candidates.extend(graph)
        if isinstance(data, list):
            candidates.extend(data)
        elif isinstance(data, dict):
            candidates.append(data)

        for obj in candidates:
            obj_type = obj.get("@type") if isinstance(obj, dict) else None
            if not obj_type:
                continue
            types = [obj_type] if isinstance(obj_type, str) else obj_type
            if not any(str(t).lower() == "recipe" for t in types):
                continue

            title = _clean_text(obj.get("name") or "")
            ingredients = _extract_ingredients(obj.get("recipeIngredient") or [])
            steps = _extract_instruction_text(obj.get("recipeInstructions") or [])
            if not title or not ingredients or not steps:
                continue

            parsed = ParsedRecipe(
                title=title,
                description=_clean_text(obj.get("description") or ""),
                source_url=url,
                image_url=_extract_image(obj.get("image")),
                tags=_coerce_keywords(obj.get("keywords")),
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


def _clean_soup_for_content(soup: BeautifulSoup) -> None:
    """Remove obvious boilerplate nodes before extracting candidate content."""
    for noisy in soup.find_all(["header", "footer", "nav", "aside", "form"]):
        noisy.decompose()
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()


def _find_main_node(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
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
    heading = container.find(string=re.compile("direction|instruction|method", re.I))
    steps: List[str] = []
    if heading and heading.parent:
        sibling = heading.parent.find_next_sibling(["ol", "ul", "p", "div"])
        if sibling:
            if sibling.name in {"ol", "ul"}:
                steps = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
            else:
                steps = [p.get_text(" ", strip=True) for p in sibling.find_all("p")] or [sibling.get_text(" ", strip=True)]
    if not steps:
        ordered_lists = container.find_all("ol")
        if ordered_lists:
            steps = [li.get_text(" ", strip=True) for li in ordered_lists[0].find_all("li")]
    return [_clean_text(s) for s in steps if _clean_text(s)]


def _build_llm_content(html: str) -> Tuple[Optional[str], str]:
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

    _clean_soup_for_content(soup)
    main_node = _find_main_node(soup)
    if not main_node:
        combined = "\n".join(script_texts)[:6000]
        return title, combined

    ingredients = _find_ingredient_items(main_node)
    instructions = _find_instruction_items(main_node)

    parts: List[str] = []
    if ingredients:
        parts.append("Ingredients:\n" + "\n".join(ingredients[:80]))
    if instructions:
        parts.append("Instructions:\n" + "\n".join(instructions[:120]))

    # Fallback: include a small slice of main text if parts are thin
    if len("\n\n".join(parts)) < 500:
        text = main_node.get_text("\n", strip=True)
        text = re.sub(r"\n{2,}", "\n", text)
        lines = text.splitlines()
        parts.append("\n".join(lines[:120]))

    combined = (title + "\n" if title else "") + "\n\n".join([p for p in parts if p])
    combined = combined[:6000]
    return title, combined


async def extract_recipe_via_llm(html: str, url: str, metadata: Optional[dict] = None) -> ParsedRecipe:
    settings = get_settings()
    if not settings.llm_base_url:
        raise ValueError("LLM_BASE_URL is not configured")

    soup = BeautifulSoup(html, "lxml")
    title, truncated_text = _build_llm_content(html)

    system_prompt = (
        "You are a recipe extraction engine. Given noisy HTML-derived text, you extract a single recipe "
        "and output ONLY strict JSON that matches the provided schema. Do not include markdown, code fences, "
        "explanations, or any text before/after the JSON. If you cannot produce valid JSON for the schema, "
        'return exactly: {"error":"invalid"}'
    )
    user_prompt = (
        f"Extract a single recipe from the page at URL: {url}\n"
        f"Page title: {title or 'Unknown'}\n"
        f"Main content (truncated):\n{truncated_text}\n\n"
        "The JSON schema you must follow is:\n"
        "{\n"
        '  "title": "string",\n'
        '  "description": "string or null",\n'
        '  "source_url": "string or null",\n'
        '  "image_url": "string or null",\n'
        '  "tags": ["string"],\n'
        '  "servings": "number or null",\n'
        '  "estimated_time_minutes": "number or null",\n'
        '  "ingredients": [\n'
        '    {\n'
        '      "text": "ingredient name only, no quantity or unit",\n'
        '      "quantity_display": "original quantity string like \\"1/2\\" or \\"2\\", or null",\n'
        '      "unit": "unit of measure like \\"cup\\", \\"tsp\\", \\"g\\", or null"\n'
        "    }\n"
        "  ],\n"
        '  "steps": ["string"],\n'
        '  "notes": ["string"]\n'
        "}\n"
        "Rules:\n"
        "- Do NOT put quantities or units into ingredient.text. Keep name only (e.g., 'all-purpose flour').\n"
        "- Put the numeric/fractional amount into quantity_display exactly as written in the source (e.g., '1/4', '2').\n"
        "- Put the unit (if any) into unit (e.g., 'cup', 'tsp'). If no unit, set unit=null.\n"
        "- If a combined string lacks a unit, still split amount into quantity_display and name into text.\n"
        "- Output ONLY one JSON object of that form."
    )

    model_name = settings.llm_full_model_name or "full"

    payload = {
        "model": model_name,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 800,
        "stream": False,
    }

    if not settings.llm_app_id or not settings.llm_app_key:
        raise ValueError("LLM app credentials are not configured")

    headers = {
        "Content-Type": "application/json",
        "X-Jarvis-App-Id": settings.llm_app_id,
        "X-Jarvis-App-Key": settings.llm_app_key,
    }

    # Background-friendly: allow longer LLM response time.
    timeout = httpx.Timeout(90.0, read=80.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{settings.llm_base_url}/v1/chat/completions", json=payload, headers=headers
        )
    response.raise_for_status()

    data = response.json()
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
            except Exception:
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
                except Exception:
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
        tags=parsed.tags or [],
    )


async def parse_recipe_from_url(url: str, use_llm_fallback: bool = True) -> ParseResult:
    warnings: List[str] = []
    try:
        html = await fetch_html(url)
    except ValueError as exc:
        return ParseResult(
            success=False,
            error_code="invalid_url",
            error_message=str(exc),
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

