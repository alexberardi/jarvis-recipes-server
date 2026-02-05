"""Ingredient extraction and cleaning utilities."""

import logging
import re
from typing import List, Optional

from jarvis_recipes.app.services.url_parsing.constants import FRACTION_CHARS
from jarvis_recipes.app.services.url_parsing.models import ParsedIngredient
from jarvis_recipes.app.services.url_parsing.parsing_utils import (
    clean_text,
    is_known_unit,
    normalize_fraction_display,
    normalize_unit_token,
)

logger = logging.getLogger(__name__)


def extract_ingredients(ingredients) -> List[ParsedIngredient]:
    """Extract ingredients from various input formats (list of strings/dicts, or string)."""
    parsed: List[ParsedIngredient] = []
    quantity_unit_re = re.compile(
        rf"^\s*([\d\s\/\.\-+{FRACTION_CHARS}]+)\s+([A-Za-z][A-Za-z\.]*)\s+(.*)$"
    )
    quantity_only_re = re.compile(rf"^\s*([\d\s\/\.\-+{FRACTION_CHARS}]+)\s+(.*)$")
    paren_cleanup_re = re.compile(r"\s*\([^)]*\)\s*")

    def clean_name(text: str) -> str:
        cleaned = clean_text(text)
        # Remove parentheses and their content
        cleaned = paren_cleanup_re.sub(" ", cleaned)
        cleaned = re.sub(r"\s*\)\s*", " ", cleaned)
        cleaned = re.sub(r"\s*\(\s*", " ", cleaned)
        cleaned = clean_text(cleaned)
        cleaned = cleaned.rstrip(" )")
        if cleaned.lower().startswith("recipe "):
            cleaned = cleaned[7:]
        return cleaned

    def split_line(line: str) -> ParsedIngredient:
        raw = clean_text(line)
        if not raw:
            return ParsedIngredient(text=raw)

        # Try qty + unit + name
        m = quantity_unit_re.match(raw)
        if m:
            qd = normalize_fraction_display(clean_text(m.group(1)))
            unit = clean_text(m.group(2))
            name = clean_name(m.group(3))
            if is_known_unit(unit):
                return ParsedIngredient(text=name, quantity_display=qd, unit=unit)

        # Try qty + name (no unit)
        m = quantity_only_re.match(raw)
        if m:
            qd = normalize_fraction_display(clean_text(m.group(1)))
            name = clean_name(m.group(2))
            return ParsedIngredient(text=name, quantity_display=qd, unit=None)

        return ParsedIngredient(text=clean_name(raw))

    if isinstance(ingredients, list):
        logger.debug("Extracting ingredients from list of %d items", len(ingredients))
        for idx, raw in enumerate(ingredients):
            if isinstance(raw, str):
                cleaned = clean_text(raw)
                if cleaned:
                    ingredient = split_line(cleaned)
                    parsed.append(ingredient)
                    logger.debug(
                        "Ingredient %d: '%s' -> text='%s', qty='%s', unit='%s'",
                        idx,
                        raw[:50],
                        ingredient.text[:30],
                        ingredient.quantity_display,
                        ingredient.unit,
                    )
                else:
                    logger.debug("Ingredient %d: string was empty after cleaning", idx)
            elif isinstance(raw, dict):
                text_val = raw.get("text") or raw.get("name")
                if text_val:
                    quantity = clean_text(raw.get("amount") or raw.get("quantity") or "")
                    unit = clean_text(raw.get("unit") or "")
                    if not quantity and not unit:
                        ingredient = split_line(text_val)
                        parsed.append(ingredient)
                    else:
                        ingredient = ParsedIngredient(
                            text=clean_text(text_val),
                            quantity_display=quantity or None,
                            unit=unit or None,
                        )
                        parsed.append(ingredient)
                    logger.debug(
                        "Ingredient %d (dict): text='%s', qty='%s', unit='%s'",
                        idx,
                        ingredient.text[:30],
                        ingredient.quantity_display,
                        ingredient.unit,
                    )
                else:
                    logger.debug("Ingredient %d: dict had no text/name field", idx)
            else:
                logger.debug("Ingredient %d: unexpected type %s", idx, type(raw).__name__)
    elif isinstance(ingredients, str):
        cleaned = clean_text(ingredients)
        if cleaned:
            parsed.append(split_line(cleaned))
    else:
        logger.warning(
            "Ingredients input is not a list or string: %s", type(ingredients).__name__
        )

    logger.info("Extracted %d ingredients from input", len(parsed))
    return parsed


def clean_parsed_ingredients(items: List[ParsedIngredient]) -> List[ParsedIngredient]:
    """Normalize ingredients: pull quantity/unit out of text if embedded; normalize fractions."""
    out: List[ParsedIngredient] = []
    quantity_unit_re = re.compile(
        rf"^\s*([\d\s\/\.\-+{FRACTION_CHARS}]+)\s+([A-Za-z][A-Za-z\.]*)\s+(.*)$"
    )
    quantity_only_re = re.compile(rf"^\s*([\d\s\/\.\-+{FRACTION_CHARS}]+)\s+(.*)$")

    def split_qty_tokens(qty: str) -> tuple[Optional[str], Optional[str]]:
        """Split a qty string like '1 pound' into ('1', 'pound') if unit recognized."""
        if not qty:
            return None, None
        tokens = qty.split()
        numeric_tokens = []
        unit_token = None
        for tok in tokens:
            if is_known_unit(tok):
                unit_token = tok
                break
            numeric_tokens.append(tok)
        if numeric_tokens:
            qd = normalize_fraction_display(" ".join(numeric_tokens))
        else:
            qd = None
        return qd, unit_token

    def split_from_text(text: str) -> tuple[Optional[str], Optional[str], str]:
        raw = clean_text(text)
        if not raw:
            return None, None, raw
        m = quantity_unit_re.match(raw)
        if m:
            qd = normalize_fraction_display(clean_text(m.group(1)))
            unit = clean_text(m.group(2))
            name = clean_text(m.group(3))
            if is_known_unit(unit):
                return qd, unit, name
        m = quantity_only_re.match(raw)
        if m:
            qd = normalize_fraction_display(clean_text(m.group(1)))
            name = clean_text(m.group(2))
            return qd, None, name
        return None, None, raw

    for ing in items:
        qty = normalize_fraction_display(ing.quantity_display)
        unit = clean_text(ing.unit) if ing.unit else None
        name = clean_text(ing.text)

        # If name starts with quantity/unit, extract
        qd2, unit2, name2 = split_from_text(name)
        if qd2 and not qty:
            qty = qd2
        if unit2 and not unit:
            unit = unit2
        name = name2 or name

        # If quantity_display itself includes a unit token, split it
        if qty and not unit:
            qd_split, unit_split = split_qty_tokens(qty)
            if unit_split and is_known_unit(unit_split):
                unit = unit_split
            if qd_split:
                qty = qd_split

        # If qty still includes the unit word, strip it
        if qty and unit:
            unit_norm = normalize_unit_token(unit)
            tokens = [t for t in qty.split() if normalize_unit_token(t) != unit_norm]
            qty = normalize_fraction_display(" ".join(tokens)) or qty

        out.append(ParsedIngredient(text=name, quantity_display=qty, unit=unit))
    return out
