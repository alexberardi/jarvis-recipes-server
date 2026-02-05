"""Recipe extractors for different parsing strategies."""

from jarvis_recipes.app.services.url_parsing.extractors.heuristic import (
    extract_recipe_heuristic,
)
from jarvis_recipes.app.services.url_parsing.extractors.llm import extract_recipe_via_llm
from jarvis_recipes.app.services.url_parsing.extractors.schema_org import (
    extract_recipe_from_schema_org,
)

__all__ = [
    "extract_recipe_from_schema_org",
    "extract_recipe_heuristic",
    "extract_recipe_via_llm",
]
