"""Heuristic recipe extraction from HTML structure."""

import re
from typing import List, Optional

from bs4 import BeautifulSoup

from jarvis_recipes.app.services.url_parsing.models import ParsedIngredient, ParsedRecipe
from jarvis_recipes.app.services.url_parsing.parsing_utils import (
    clean_text,
    parse_servings_from_text,
)


def _find_ingredient_items(container) -> List[str]:
    """Find likely ingredient items in a container element."""
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
            if re.search(
                r"\d|\b(cup|tsp|tbsp|tablespoon|teaspoon|ounce|oz|gram|kg|ml|l)\b",
                item,
                flags=re.I,
            )
        )
        score = matches * 2 + len(items)
        if score > best_score:
            best_score = score
            best_items = items
    return [clean_text(i) for i in best_items if clean_text(i)]


def _find_instruction_items(container) -> List[str]:
    """Find likely instruction items in a container element."""
    steps: List[str] = []

    # Strategy 1: Look for ordered lists
    ordered_lists = container.find_all("ol")
    if ordered_lists:
        best_list = None
        best_score = 0
        for ol in ordered_lists:
            items = [li.get_text(" ", strip=True) for li in ol.find_all("li")]
            score = len(items)
            action_verbs = sum(
                1
                for item in items
                if re.search(
                    r"\b(cook|bake|add|mix|stir|heat|pour|season|chop|slice|dice|mince|preheat)\b",
                    item,
                    re.I,
                )
            )
            score += action_verbs * 2
            if score > best_score and len(items) >= 3:
                best_score = score
                best_list = ol
        if best_list:
            steps = [li.get_text(" ", strip=True) for li in best_list.find_all("li")]
            return [clean_text(s) for s in steps if clean_text(s)]

    # Strategy 2: Look for instruction headings
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
            sibling = heading.parent.find_next_sibling(["ol", "ul", "div", "section"])
            if sibling:
                if sibling.name in {"ol", "ul"}:
                    steps = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
                else:
                    paragraphs = sibling.find_all("p")
                    if paragraphs:
                        steps = [p.get_text(" ", strip=True) for p in paragraphs]
                    else:
                        list_items = sibling.find_all("li")
                        if list_items:
                            steps = [
                                li.get_text(" ", strip=True) for li in list_items
                            ]
                        else:
                            headings = sibling.find_all(["h2", "h3", "h4", "strong", "b"])
                            for h in headings:
                                step_text = h.get_text(" ", strip=True)
                                next_elem = h.find_next_sibling()
                                if next_elem and next_elem.name not in [
                                    "h2",
                                    "h3",
                                    "h4",
                                    "strong",
                                    "b",
                                ]:
                                    step_text += " " + next_elem.get_text(" ", strip=True)
                                if step_text and len(step_text) > 10:
                                    steps.append(step_text)
            if steps:
                break

    # Strategy 3: Look for structured recipe steps
    if not steps:
        step_headings = container.find_all(
            string=re.compile(
                r"^(step\s+\d+|cook|bake|make|prep|prepare|season|add|mix|stir|heat|pour)",
                re.I,
            )
        )
        for heading_text in step_headings:
            parent = heading_text.parent
            if parent:
                step_content = parent.get_text(" ", strip=True)
                next_sib = parent.find_next_sibling()
                if next_sib:
                    step_content += " " + next_sib.get_text(" ", strip=True)
                if step_content and len(step_content) > 20:
                    steps.append(step_content)

    # Strategy 4: Fallback - any ordered list
    if not steps:
        ordered_lists = container.find_all("ol")
        if ordered_lists:
            longest = max(ordered_lists, key=lambda ol: len(ol.find_all("li")))
            steps = [li.get_text(" ", strip=True) for li in longest.find_all("li")]

    return [clean_text(s) for s in steps if clean_text(s)]


def extract_recipe_heuristic(html: str, url: str) -> Optional[ParsedRecipe]:
    """Extract recipe using heuristic HTML analysis."""
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("h1") or soup.title
    title = clean_text(title_tag.get_text()) if title_tag else None

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
            if re.search(
                r"\d|\b(cup|tsp|tbsp|tablespoon|teaspoon|ounce|gram|kg|ml|l)\b",
                item,
                flags=re.I,
            )
        )
        if matches >= max(2, len(items) // 2):
            best_ingredients = items
            break
    ingredients = [
        ParsedIngredient(text=clean_text(i)) for i in best_ingredients if clean_text(i)
    ]

    instruction_heading = container.find(
        string=re.compile("direction|instruction|method", re.I)
    )
    steps: List[str] = []
    if instruction_heading and instruction_heading.parent:
        sibling = instruction_heading.parent.find_next_sibling(
            ["ol", "ul", "p", "div"]
        )
        if sibling:
            if sibling.name in {"ol", "ul"}:
                steps = [li.get_text(" ", strip=True) for li in sibling.find_all("li")]
            else:
                steps = [
                    p.get_text(" ", strip=True) for p in sibling.find_all("p")
                ] or [sibling.get_text(" ", strip=True)]
    if not steps:
        ordered_lists = container.find_all("ol")
        if ordered_lists:
            steps = [
                li.get_text(" ", strip=True) for li in ordered_lists[0].find_all("li")
            ]

    steps = [clean_text(s) for s in steps if clean_text(s)]
    servings = parse_servings_from_text(container.get_text(" ", strip=True))

    if title and ingredients and steps:
        return ParsedRecipe(
            title=title, source_url=url, ingredients=ingredients, steps=steps, servings=servings
        )
    return None


def clean_soup_for_content(soup: BeautifulSoup) -> None:
    """Remove obvious boilerplate nodes before extracting candidate content."""
    for noisy in soup.find_all(["header", "footer", "nav", "aside", "form"]):
        noisy.decompose()
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()


def find_main_node(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Find the main content node in the soup."""
    return (
        soup.find(attrs={"itemtype": re.compile("Recipe", re.I)})
        or soup.find("article")
        or soup.find("main")
        or soup.body
    )
