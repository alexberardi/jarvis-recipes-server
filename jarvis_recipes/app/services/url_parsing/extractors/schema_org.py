"""Schema.org JSON-LD recipe extraction."""

import json
import logging
from typing import Optional

from bs4 import BeautifulSoup

from jarvis_recipes.app.services.url_parsing.ingredient_parser import extract_ingredients
from jarvis_recipes.app.services.url_parsing.models import ParsedRecipe
from jarvis_recipes.app.services.url_parsing.parsing_utils import (
    clean_text,
    coerce_keywords,
    extract_image,
    extract_instruction_text,
    parse_minutes,
    parse_servings,
)

logger = logging.getLogger(__name__)


def extract_recipe_from_schema_org(html: str, url: str) -> Optional[ParsedRecipe]:
    """Extract recipe from schema.org JSON-LD data embedded in HTML."""
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
            logger.warning(
                "JSON-LD block %d failed to parse: %s (first 200 chars: %s)",
                idx,
                exc,
                raw_json[:200],
            )
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
                logger.debug(
                    "Candidate %d is not a Recipe (type: %s), skipping", obj_idx, type_str
                )
                continue

            title = clean_text(obj.get("name") or "")
            recipe_ingredient_raw = obj.get("recipeIngredient") or []
            recipe_instructions_raw = obj.get("recipeInstructions") or []

            logger.debug(
                "Recipe candidate %d raw data: recipeIngredient type=%s, len=%s, recipeInstructions type=%s, len=%s",
                obj_idx,
                type(recipe_ingredient_raw).__name__,
                len(recipe_ingredient_raw)
                if isinstance(recipe_ingredient_raw, (list, str))
                else "N/A",
                type(recipe_instructions_raw).__name__,
                len(recipe_instructions_raw)
                if isinstance(recipe_instructions_raw, (list, str))
                else "N/A",
            )

            ingredients = extract_ingredients(recipe_ingredient_raw)
            steps = extract_instruction_text(recipe_instructions_raw)

            ingredients = ingredients or []
            steps = steps or []

            logger.info(
                "Recipe candidate %d: title=%s, ingredients=%d, steps=%d",
                obj_idx,
                title[:50] if title else "None",
                len(ingredients),
                len(steps),
            )

            if not title:
                logger.warning("Recipe candidate %d missing title", obj_idx)
                continue
            if not ingredients:
                logger.warning(
                    "Recipe candidate %d missing ingredients (raw had %d items)",
                    obj_idx,
                    len(recipe_ingredient_raw)
                    if isinstance(recipe_ingredient_raw, list)
                    else 1
                    if recipe_ingredient_raw
                    else 0,
                )
                if (
                    recipe_ingredient_raw
                    and isinstance(recipe_ingredient_raw, list)
                    and len(recipe_ingredient_raw) > 0
                ):
                    logger.warning(
                        "First ingredient raw: %s", str(recipe_ingredient_raw[0])[:200]
                    )
                continue
            if not steps:
                logger.warning(
                    "Recipe candidate %d missing steps (raw had %d items)",
                    obj_idx,
                    len(recipe_instructions_raw)
                    if isinstance(recipe_instructions_raw, list)
                    else 1
                    if recipe_instructions_raw
                    else 0,
                )
                continue

            # Extract tags
            keywords = obj.get("keywords")
            recipe_category = obj.get("recipeCategory") or []
            recipe_cuisine = obj.get("recipeCuisine") or []

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

            tags = coerce_keywords(
                all_keywords if all_keywords else None, recipe_title=title
            )

            parsed = ParsedRecipe(
                title=title,
                description=clean_text(obj.get("description") or ""),
                source_url=url,
                image_url=extract_image(obj.get("image")),
                tags=tags,
                servings=parse_servings(obj.get("recipeYield")),
                estimated_time_minutes=parse_minutes(obj.get("totalTime")),
                ingredients=ingredients,
                steps=steps,
            )
            return parsed
    return None
