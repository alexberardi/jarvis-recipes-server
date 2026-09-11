"""Tests for the cookbook seed script.

Same shape as the household-meals script, plus the things specific to data that
came out of a PDF: the parse could have produced empty steps, orphaned column
fragments, or brand plugs, and none of that is visible from a row count.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from jarvis_recipes.app.db import models

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_cookbook_recipes.py"

USER = "1"
HOUSEHOLD = "47410f36-aec6-4b4c-a08c-05ac84db2904"


@pytest.fixture(scope="module")
def seeder():
    spec = importlib.util.spec_from_file_location("_seed_cookbook_recipes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_recipes_exist(seeder):
    assert len(seeder.RECIPES) >= 15


def test_every_recipe_is_complete(seeder):
    for r in seeder.RECIPES:
        assert r["title"].strip(), "empty title"
        assert r["serving"] >= 1, f"{r['title']}: no servings"
        assert r["minutes"] > 0, f"{r['title']}: no time"
        assert len(r["ingredients"]) >= 5, f"{r['title']}: {len(r['ingredients'])} ingredients"
        assert len(r["steps"]) >= 3, f"{r['title']}: {len(r['steps'])} steps"


def test_titles_are_unique(seeder):
    titles = [r["title"] for r in seeder.RECIPES]
    dupes = {t for t in titles if titles.count(t) > 1}
    assert dupes == set(), f"duplicate titles: {dupes}"


def test_no_brand_plugs_survived(seeder):
    """The cookbook sells a flour and embeds an affiliate code."""
    banned = re.compile(r"shredz|fit flour|code\s*:", re.I)
    offenders = []
    for r in seeder.RECIPES:
        # The source attribution is allowed to name the book; ingredients and
        # steps are not.
        haystack = " ".join([r["title"], *(i[0] for i in r["ingredients"]), *r["steps"]])
        if banned.search(haystack):
            offenders.append(r["title"])
    assert offenders == [], f"brand references: {offenders}"


def test_no_column_seam_artifacts(seeder):
    """The pages are two-column; a bad seam leaves orphans mid-text.

    Real examples this caught during extraction: "lean 93/7 Ground   2 Beef"
    (a step number) and "diced tomatoes N and green chilis" (the first letter
    of "Nutritional Values" from the next block).
    """
    problems = []
    for r in seeder.RECIPES:
        for text, *_ in r["ingredients"]:
            if re.search(r"\s{2,}", text):
                problems.append(f"{r['title']}: double space in {text!r}")
            if re.search(r"\s[A-Z]\s", text):
                problems.append(f"{r['title']}: stray capital in {text!r}")
        for step in r["steps"]:
            if re.search(r"nutrition", step, re.I):
                problems.append(f"{r['title']}: macro block leaked into a step")
    assert problems == [], f"parse artifacts: {problems}"


def test_steps_do_not_start_mid_sentence(seeder):
    """A mis-split step begins with a conjunction instead of an instruction."""
    bad = [
        f"{r['title']}: {s[:40]!r}"
        for r in seeder.RECIPES
        for s in r["steps"]
        if re.match(r"^(and|or|with|the|until|then)\b", s, re.I)
    ]
    assert bad == [], f"steps starting mid-sentence: {bad}"


def test_quantities_are_mostly_parsed(seeder):
    """Amounts belong in their own columns so the grocery list can use them.

    Not all of them: "Salt & pepper, to taste" has no amount, and inventing one
    would be worse than leaving it null.
    """
    total = sum(len(r["ingredients"]) for r in seeder.RECIPES)
    with_qty = sum(1 for r in seeder.RECIPES for i in r["ingredients"] if i[1])
    assert with_qty / total > 0.75, f"only {with_qty}/{total} ingredients have a quantity"


def test_parsed_quantities_are_numeric_and_positive(seeder):
    for r in seeder.RECIPES:
        for text, display, value, _unit in r["ingredients"]:
            if display:
                assert value is not None, f"{r['title']}: {text!r} has a display but no value"
                assert value > 0, f"{r['title']}: {text!r} has value {value}"


def test_seed_writes_recipes_with_household_visibility(seeder, db_session):
    created, skipped = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    assert created == len(seeder.RECIPES)
    assert skipped == 0
    for recipe in db_session.query(models.Recipe).all():
        assert recipe.household_id == HOUSEHOLD, f"{recipe.title} is author-only"
        assert recipe.ingredients and recipe.steps
        numbers = [s.step_number for s in recipe.steps]
        assert numbers == sorted(numbers) and numbers[0] == 1


def test_every_recipe_is_tagged_alex(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    for recipe in db_session.query(models.Recipe).all():
        names = {t.name for t in recipe.tags}
        assert seeder.PICK_TAG in names, f"{recipe.title} is missing the {seeder.PICK_TAG} tag"
        assert "dinner" in names


def test_description_credits_the_source_with_a_page(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    for recipe in db_session.query(models.Recipe).all():
        assert seeder.SOURCE in recipe.description
        assert re.search(r"p\.\d+", recipe.description), recipe.description


def test_seed_is_idempotent(seeder, db_session):
    first, _ = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    created, skipped = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    assert created == 0
    assert skipped == first


def test_dry_run_writes_nothing(seeder, db_session):
    created, _ = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=True)
    assert created == len(seeder.RECIPES)
    assert db_session.query(models.Recipe).count() == 0


def test_purge_removes_only_these_titles(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    mine = models.Recipe(
        user_id=USER, household_id=HOUSEHOLD, title="Nonna's Sunday Gravy",
        source_type=models.SourceType.MANUAL, servings=6,
    )
    db_session.add(mine)
    db_session.commit()

    removed = seeder.purge(db_session, dry_run=False, user_id=USER)

    assert removed == len(seeder.RECIPES)
    assert [r.title for r in db_session.query(models.Recipe).all()] == ["Nonna's Sunday Gravy"]


def test_purge_does_not_key_on_the_alex_tag(seeder, db_session):
    """"Alex" is a tag the household uses, not a marker this script owns."""
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    tagged_by_hand = models.Recipe(
        user_id=USER, household_id=HOUSEHOLD, title="Dad's Grilled Wings",
        source_type=models.SourceType.MANUAL, servings=4,
    )
    db_session.add(tagged_by_hand)
    tagged_by_hand.tags.append(seeder._get_tag(db_session, seeder.PICK_TAG))
    db_session.commit()

    seeder.purge(db_session, dry_run=False, user_id=USER)

    survivors = [r.title for r in db_session.query(models.Recipe).all()]
    assert survivors == ["Dad's Grilled Wings"], "purge took a hand-tagged recipe"


def test_no_group_headers_leaked_into_ingredients(seeder):
    """The book labels ingredient groups in several styles.

    "FOR THE CHICKEN:" was recognised during extraction; "Beef Mixture:",
    "Breading:" and "Nashville Hot Oil (Brush-On Mix):" were not, so they ended
    up appended to the preceding ingredient ("salt High-Protein Creamy Herb
    Drizzle:") or standing alone as a fake one.
    """
    import re as _re

    problems = []
    for r in seeder.RECIPES:
        for text, *_ in r["ingredients"]:
            if _re.match(r"^[A-Z][\w &'()\-/]*:$", text):
                problems.append(f"{r['title']}: header as ingredient {text!r}")
            if _re.search(r"\s+[A-Z][\w &'()\-/]{2,}:$", text):
                problems.append(f"{r['title']}: header appended to {text!r}")
    assert problems == [], f"group headers in the ingredient list: {problems}"


def test_no_ingredient_holds_two_items(seeder):
    """Unbulleted trailing items folded into the line above during extraction.

    Real example: "Salt & black pepper, to taste Garlic powder, to taste Oil
    spray" arrived as ONE ingredient, so it would have shown as one nonsense row
    in the app and one nonsense line on the shopping list.
    """
    import re as _re

    bad = [
        f"{r['title']}: {text!r}"
        for r in seeder.RECIPES
        for text, *_ in r["ingredients"]
        if len(_re.findall(r"to taste", text, _re.I)) > 1
        or _re.search(r"to taste\s+\S", text, _re.I)
    ]
    assert bad == [], f"ingredients holding more than one item: {bad}"


def test_no_unbulleted_ingredient_was_folded_into_its_neighbour(seeder):
    """The book does not bullet every list, so items ran together.

    Real examples: "...ginger (5g) Green onions, for topping" and
    "honey (or agave) Handful fresh cilantro Pinch of salt". Detected as a
    capitalised word immediately after a closing parenthesis -- which no single
    ingredient legitimately contains, unlike mid-text capitals such as "Greek
    yogurt" or "Parmesan".
    """
    import re as _re

    bad = [
        f"{r['title']}: {text!r}"
        for r in seeder.RECIPES
        for text, *_ in r["ingredients"]
        if _re.search(r"\)\s+[A-Z][a-z]", text)
    ]
    assert bad == [], f"ingredients holding a folded-in neighbour: {bad}"


def test_no_instruction_text_sits_in_the_ingredient_list(seeder):
    """One page prints "👉 Blend all sauce ingredients" inside the ingredients."""
    bad = [
        f"{r['title']}: {text!r}"
        for r in seeder.RECIPES
        for text, *_ in r["ingredients"]
        if "👉" in text or len(text) > 90
    ]
    assert bad == [], f"instruction text among the ingredients: {bad}"


def test_brands_the_cookbook_names_are_gone(seeder):
    """Beyond the flour: the book also names a rice blend by brand."""
    import re as _re

    banned = _re.compile(r"seeds of change|shredz|fit flour|code\s*:", _re.I)
    hits = [
        f"{r['title']}: {text!r}"
        for r in seeder.RECIPES
        for text, *_ in r["ingredients"]
        if banned.search(text)
    ]
    assert hits == [], f"brand references: {hits}"


def test_no_ingredient_reads_as_two_items(seeder):
    """Catch-all for the unbulleted-merge class, reviewed case by case.

    The shape "lowercase Capital" cannot be auto-split -- "non-fat Greek
    yogurt", "grated Parmesan" and "hot sauce (like Frank's)" are single
    ingredients. So this pins the specific merges found by hand instead, and
    fails if any reappear.
    """
    known_bad = [
        "olive oil or cooking spray Worcestershire",
        "pico de gallo Pickled",
        "cubed Light cooking spray",
        "olive oil Hot sauce",
        "syrup Splash of pickle juice",
        "salt Chipotle Yogurt Sauce",
        "salt Garlic Herb Sauce",
    ]
    texts = [t for r in seeder.RECIPES for t, *_ in r["ingredients"]]
    for fragment in known_bad:
        assert not any(fragment in t for t in texts), f"merged ingredient back: {fragment!r}"
