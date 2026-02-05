"""URL recipe parsing package.

This package provides functionality for extracting recipes from URLs using
multiple strategies: schema.org JSON-LD, heuristic HTML parsing, and LLM fallback.
"""

from jarvis_recipes.app.services.url_parsing.html_fetcher import (
    fetch_html,
    is_private_host,
    preflight_validate_url,
)
from jarvis_recipes.app.services.url_parsing.ingredient_parser import (
    clean_parsed_ingredients,
    extract_ingredients,
)
from jarvis_recipes.app.services.url_parsing.models import (
    ParsedIngredient,
    ParsedRecipe,
    ParseResult,
    PreflightResult,
)
from jarvis_recipes.app.services.url_parsing.parsing_utils import (
    clean_text,
    coerce_keywords,
    extract_image,
    extract_instruction_text,
    is_known_unit,
    normalize_fraction_display,
    normalize_unit_token,
    parse_iso8601_duration,
    parse_minutes,
    parse_servings,
    parse_servings_from_text,
)

__all__ = [
    # Models
    "ParsedIngredient",
    "ParsedRecipe",
    "ParseResult",
    "PreflightResult",
    # HTML fetching
    "fetch_html",
    "is_private_host",
    "preflight_validate_url",
    # Ingredient parsing
    "clean_parsed_ingredients",
    "extract_ingredients",
    # Parsing utilities
    "clean_text",
    "coerce_keywords",
    "extract_image",
    "extract_instruction_text",
    "is_known_unit",
    "normalize_fraction_display",
    "normalize_unit_token",
    "parse_iso8601_duration",
    "parse_minutes",
    "parse_servings",
    "parse_servings_from_text",
]
