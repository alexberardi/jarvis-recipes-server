"""General parsing utilities for recipe extraction."""

import re
from typing import List, Optional, Sequence

from jarvis_recipes.app.services.url_parsing.constants import COMMON_UNITS, FRACTION_MAP


def clean_text(text: str) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", text or "").strip()


def normalize_unit_token(unit: str) -> str:
    """Normalize a unit token for comparison."""
    token = unit.lower().strip(".")
    if token.endswith("s"):
        token = token[:-1]
    return token


def is_known_unit(unit: str) -> bool:
    """Check if a unit string is a recognized cooking unit."""
    return normalize_unit_token(unit) in COMMON_UNITS


def normalize_fraction_display(qty: Optional[str]) -> Optional[str]:
    """Normalize fraction characters and quantity display strings."""
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


def parse_iso8601_duration(duration: str) -> Optional[int]:
    """Parse a minimal ISO-8601 duration string (e.g., PT1H30M) into minutes."""
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


def parse_minutes(value) -> Optional[int]:
    """Parse a minutes value from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        iso_minutes = parse_iso8601_duration(value)
        if iso_minutes is not None:
            return iso_minutes
        match = re.search(r"(\d+)\s*(min|minute|minutes)", value, flags=re.I)
        if match:
            return int(match.group(1))
    return None


def parse_servings(value) -> Optional[int]:
    """Parse servings from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def parse_servings_from_text(text: str) -> Optional[int]:
    """Extract servings from descriptive text."""
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


def extract_image(value) -> Optional[str]:
    """Extract image URL from various schema.org image formats."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        for item in value:
            if isinstance(item, str):
                return item
    return None


def extract_instruction_text(instructions) -> List[str]:
    """Extract step text from various instruction formats."""
    steps: List[str] = []
    if isinstance(instructions, list):
        for entry in instructions:
            if isinstance(entry, str):
                cleaned = clean_text(entry)
                if cleaned:
                    steps.append(cleaned)
            elif isinstance(entry, dict):
                text_val = entry.get("text") or entry.get("description")
                cleaned = clean_text(text_val or "")
                if cleaned:
                    steps.append(cleaned)
    elif isinstance(instructions, str):
        cleaned = clean_text(instructions)
        if cleaned:
            steps.append(cleaned)
    return steps


def coerce_keywords(value, recipe_title: Optional[str] = None) -> List[str]:
    """Extract and filter tags from keywords, keeping only general categories."""
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

    # Normalize recipe title for comparison
    title_words = set()
    if recipe_title:
        title_normalized = recipe_title.lower()
        for word in ["recipe", "recipes", "how to", "how to make", "easy", "best", "homemade"]:
            title_normalized = title_normalized.replace(word, "")
        title_words = set(title_normalized.split())
        title_words = {w for w in title_words if len(w) > 3}

    # Filter tags: keep only general categories
    filtered_tags = []
    for tag in raw_tags:
        tag_lower = tag.lower().strip()

        if not tag_lower:
            continue

        # Skip tags too similar to recipe title
        if title_words:
            tag_words = set(tag_lower.split())
            overlap = len(tag_words & title_words)
            if overlap >= 2:
                continue
            if tag_lower in recipe_title.lower() or recipe_title.lower() in tag_lower:
                continue

        # Skip recipe name variations
        if recipe_title and len(tag_lower.split()) >= 3:
            if any(word in tag_lower for word in title_words if len(word) > 4):
                continue

        # Keep general categories
        if len(tag_lower.split()) <= 2:
            filtered_tags.append(tag)
        elif any(
            category in tag_lower
            for category in [
                "free",
                "friendly",
                "diet",
                "cuisine",
                "course",
                "meal",
                "type",
                "vegetarian",
                "vegan",
                "gluten",
                "dairy",
                "nut",
                "paleo",
                "keto",
                "breakfast",
                "lunch",
                "dinner",
                "dessert",
                "appetizer",
                "snack",
                "american",
                "italian",
                "mexican",
                "asian",
                "french",
                "indian",
                "chinese",
                "quick",
                "slow",
                "cooker",
                "instant",
                "one-pot",
                "sheet-pan",
            ]
        ):
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
