"""URL recipe parsing - main module.

This module provides the main API for parsing recipes from URLs.
The heavy lifting is delegated to sub-modules in the url_parsing package.
"""

import json
import logging
from typing import List

import httpx  # noqa: F401 - exposed for test monkeypatching

from jarvis_recipes.app.core.config import get_settings  # noqa: F401 - re-export for tests
from jarvis_recipes.app.db.models import SourceType
from jarvis_recipes.app.schemas.recipe import IngredientCreate, RecipeCreate, StepCreate

# Re-export models for backward compatibility
from jarvis_recipes.app.services.url_parsing.models import (
    ParsedIngredient,
    ParsedRecipe,
    ParseResult,
    PreflightResult,
)

# Import from sub-modules
from jarvis_recipes.app.services.url_parsing.html_fetcher import (
    fetch_html,
    preflight_validate_url,
)
from jarvis_recipes.app.services.url_parsing.ingredient_parser import (
    clean_parsed_ingredients,
)
from jarvis_recipes.app.services.url_parsing.parsing_utils import (
    coerce_keywords,
    normalize_fraction_display,
)
from jarvis_recipes.app.services.url_parsing.extractors.schema_org import (
    extract_recipe_from_schema_org,
)
from jarvis_recipes.app.services.url_parsing.extractors.heuristic import (
    clean_soup_for_content,
    extract_recipe_heuristic,
    find_main_node,
)
from jarvis_recipes.app.services.url_parsing.extractors.llm import (
    extract_recipe_via_llm,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    # Models
    "ParsedIngredient",
    "ParsedRecipe",
    "ParseResult",
    "PreflightResult",
    # Main functions
    "parse_recipe_from_url",
    "normalize_parsed_recipe",
    "preflight_validate_url",
    "fetch_html",
    # Extractors
    "extract_recipe_from_schema_org",
    "extract_recipe_from_microdata",
    "extract_recipe_heuristic",
    "extract_recipe_via_llm",
    # HTML utilities (used by ingestion_service)
    "clean_soup_for_content",
    "find_main_node",
    # Re-exports for backward compatibility / tests
    "get_settings",
    "httpx",
]


def extract_recipe_from_microdata(html: str, url: str) -> None:
    """Placeholder for microdata/RDFa parsing. Implement as needed."""
    return None


def normalize_parsed_recipe(parsed: ParsedRecipe) -> RecipeCreate:
    """Convert a ParsedRecipe to a RecipeCreate schema for database storage."""
    ingredients = [
        IngredientCreate(
            text=item.text,
            quantity_display=normalize_fraction_display(item.quantity_display),
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
        tags=coerce_keywords(parsed.tags, recipe_title=parsed.title) if parsed.tags else [],
    )


async def parse_recipe_from_url(url: str, use_llm_fallback: bool = True) -> ParseResult:
    """Parse a recipe from a URL using multiple extraction strategies.

    Strategies are tried in order:
    1. Schema.org JSON-LD
    2. Microdata (placeholder)
    3. Heuristic HTML parsing
    4. LLM fallback (if enabled)

    Args:
        url: The URL to parse
        use_llm_fallback: Whether to use LLM as a last resort

    Returns:
        ParseResult with success status and parsed recipe or error details
    """
    warnings: List[str] = []

    # Fetch HTML
    try:
        html = await fetch_html(url)
    except ValueError as exc:
        error_msg = str(exc)
        is_encoding_error = (
            "encoding" in error_msg.lower()
            or "corrupted" in error_msg.lower()
            or "invalid encoding" in error_msg.lower()
        )

        if is_encoding_error:
            logger.warning(
                "Encoding/corruption error for %s: %s. Suggesting webview fallback.",
                url,
                error_msg,
            )
            return ParseResult(
                success=False,
                error_code="fetch_failed",
                error_message=error_msg,
                warnings=warnings + ["encoding_error"],
                next_action="webview_extract",
                next_action_reason="encoding_error",
            )
        else:
            return ParseResult(
                success=False,
                error_code="invalid_url",
                error_message=error_msg,
                warnings=warnings,
            )
    except httpx.HTTPStatusError as exc:
        logger.exception(
            "Failed to fetch URL %s (status=%s)",
            url,
            exc.response.status_code if exc.response else "unknown",
        )
        is_blocked = exc.response and exc.response.status_code in (401, 403)
        return ParseResult(
            success=False,
            error_code="fetch_failed",
            error_message=f"status_{exc.response.status_code if exc.response else 'unknown'}",
            warnings=warnings + ["blocked_by_site" if is_blocked else "fetch_http_error"],
            next_action="webview_extract" if is_blocked else None,
            next_action_reason="blocked_by_site" if is_blocked else None,
        )
    except httpx.HTTPError as exc:
        logger.exception("Failed to fetch URL %s", url)
        return ParseResult(
            success=False,
            error_code="fetch_failed",
            error_message=str(exc),
            warnings=warnings + ["fetch_http_error"],
        )

    # Try schema.org JSON-LD
    parsed = extract_recipe_from_schema_org(html, url)
    if parsed:
        parsed.ingredients = clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(
            success=True,
            recipe=parsed,
            used_llm=False,
            parser_strategy="schema_org_json_ld",
            warnings=warnings,
        )

    # Try microdata (placeholder)
    parsed = extract_recipe_from_microdata(html, url)
    if parsed:
        parsed.ingredients = clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(
            success=True,
            recipe=parsed,
            used_llm=False,
            parser_strategy="microdata",
            warnings=warnings,
        )

    # Try heuristic parsing
    parsed = extract_recipe_heuristic(html, url)
    if parsed:
        parsed.ingredients = clean_parsed_ingredients(parsed.ingredients)
        return ParseResult(
            success=True,
            recipe=parsed,
            used_llm=False,
            parser_strategy="heuristic",
            warnings=warnings,
        )

    # Try LLM fallback
    if use_llm_fallback:
        try:
            parsed = await extract_recipe_via_llm(html, url, metadata={"length": len(html)})
            warnings.append("LLM fallback used; please verify ingredients.")
            parsed.ingredients = clean_parsed_ingredients(parsed.ingredients)
            return ParseResult(
                success=True,
                recipe=parsed,
                used_llm=True,
                parser_strategy="llm_fallback",
                warnings=warnings,
            )
        except ValueError as exc:
            error_msg = str(exc)
            if "corrupted" in error_msg.lower() or "encoding" in error_msg.lower():
                logger.warning(
                    "Encoding error detected in LLM path for %s: %s. Suggesting webview fallback.",
                    url,
                    error_msg,
                )
                return ParseResult(
                    success=False,
                    error_code="fetch_failed",
                    error_message=error_msg,
                    warnings=warnings + ["encoding_error"],
                    next_action="webview_extract",
                    next_action_reason="encoding_error",
                )
            else:
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
        except json.JSONDecodeError:
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

    return ParseResult(
        success=False,
        error_code="parse_failed",
        error_message="Unable to parse recipe",
        warnings=warnings,
    )
