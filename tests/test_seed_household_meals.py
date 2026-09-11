"""Tests for the household meal seed script.

It is a one-off that runs against prod, so the things worth proving are the
ones that are expensive to get wrong there: it writes what it claims, it can be
re-run, --dry-run really writes nothing, and --purge removes only its own rows.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from jarvis_recipes.app.db import models

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_household_meals.py"

USER = "1"
HOUSEHOLD = "47410f36-aec6-4b4c-a08c-05ac84db2904"


@pytest.fixture(scope="module")
def seeder():
    spec = importlib.util.spec_from_file_location("_seed_household_meals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_script_exists(seeder):
    assert seeder.RECIPES, "no recipes defined"


def test_every_recipe_is_complete(seeder):
    """A title with no ingredients or steps is not what was asked for."""
    for title, minutes, ingredients, steps, _tags in seeder.RECIPES:
        assert title.strip(), "empty title"
        assert minutes > 0, f"{title}: no time"
        assert len(ingredients) >= 3, f"{title}: only {len(ingredients)} ingredients"
        assert len(steps) >= 3, f"{title}: only {len(steps)} steps"
        for text, _display, _value, _unit in ingredients:
            assert text and text.strip(), f"{title}: blank ingredient"


def test_titles_are_unique(seeder):
    """Idempotency keys on (user_id, title), so a duplicate would silently skip."""
    titles = [r[0] for r in seeder.RECIPES]
    duplicates = {t for t in titles if titles.count(t) > 1}
    assert duplicates == set(), f"duplicate titles: {duplicates}"


def test_no_brands_or_stores(seeder):
    """The source list named products; these were asked to stay generic."""
    banned = ["knorr", "costco", "real good", "trader joe", "walmart", "kirkland"]
    offenders = []
    for title, _minutes, ingredients, steps, _tags in seeder.RECIPES:
        haystack = " ".join([title, *(i[0] for i in ingredients), *steps]).lower()
        for brand in banned:
            if brand in haystack:
                offenders.append(f"{title}: {brand}")
    assert offenders == [], f"brand references: {offenders}"


def test_seed_writes_recipes_with_household_visibility(seeder, db_session):
    created, skipped = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    assert created == len(seeder.RECIPES)
    assert skipped == 0

    recipes = db_session.query(models.Recipe).all()
    assert len(recipes) == len(seeder.RECIPES)
    for recipe in recipes:
        # household_id is what makes it visible to the other person in the
        # house; user_id alone is only authorship.
        assert recipe.household_id == HOUSEHOLD, f"{recipe.title} is author-only"
        assert recipe.user_id == USER
        assert recipe.servings == seeder.SERVINGS
        assert recipe.ingredients, f"{recipe.title} has no ingredients"
        assert recipe.steps, f"{recipe.title} has no steps"
        # Steps must be ordered, or the instructions are shuffled in the app.
        numbers = [s.step_number for s in recipe.steps]
        assert numbers == sorted(numbers) and numbers[0] == 1


def test_seed_creates_the_local_user_row(seeder, db_session):
    assert db_session.get(models.User, USER) is None
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    assert db_session.get(models.User, USER) is not None


def test_seed_is_idempotent(seeder, db_session):
    first, _ = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    created, skipped = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    assert created == 0, "re-running created duplicates"
    assert skipped == first
    assert db_session.query(models.Recipe).count() == first


def test_dry_run_writes_nothing(seeder, db_session):
    created, _ = seeder.seed(db_session, USER, HOUSEHOLD, dry_run=True)

    assert created == len(seeder.RECIPES), "dry run should still report the plan"
    assert db_session.query(models.Recipe).count() == 0, "dry run wrote rows"


def test_every_recipe_carries_the_seed_tag(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    for recipe in db_session.query(models.Recipe).all():
        names = {t.name for t in recipe.tags}
        assert seeder.SEED_TAG in names, f"{recipe.title} is not purgeable"
        assert "dinner" in names


def test_purge_removes_only_seeded_rows(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)

    # Something the user added themselves, which purge must not touch.
    mine = models.Recipe(
        user_id=USER, household_id=HOUSEHOLD, title="Nonna's Sunday Gravy",
        source_type=models.SourceType.MANUAL, servings=6,
    )
    db_session.add(mine)
    db_session.commit()

    removed = seeder.purge(db_session, dry_run=False)

    assert removed == len(seeder.RECIPES)
    survivors = [r.title for r in db_session.query(models.Recipe).all()]
    assert survivors == ["Nonna's Sunday Gravy"]


def test_purge_dry_run_deletes_nothing(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False)
    before = db_session.query(models.Recipe).count()

    seeder.purge(db_session, dry_run=True)

    assert db_session.query(models.Recipe).count() == before


# --- the marker tag is optional -------------------------------------------
#
# The tag is bookkeeping, not something the cook wants in the app's tag picker
# next to "dinner" and "steak" -- so prod seeds without it. That removes what
# --purge normally keys on, hence the title fallback below.


def test_no_seed_tag_keeps_the_tag_picker_clean(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False, seed_tag=False)

    for recipe in db_session.query(models.Recipe).all():
        names = {t.name for t in recipe.tags}
        assert seeder.SEED_TAG not in names, f"{recipe.title} still carries the marker"
        # The tags a cook actually wants must survive.
        assert "dinner" in names

    assert (
        db_session.query(models.Tag).filter(models.Tag.name == seeder.SEED_TAG).first()
        is None
    ), "the marker tag was created even though it was not applied"


def test_purge_falls_back_to_titles_when_untagged(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False, seed_tag=False)

    mine = models.Recipe(
        user_id=USER, household_id=HOUSEHOLD, title="Nonna's Sunday Gravy",
        source_type=models.SourceType.MANUAL, servings=6,
    )
    db_session.add(mine)
    db_session.commit()

    removed = seeder.purge(db_session, dry_run=False, user_id=USER)

    assert removed == len(seeder.RECIPES)
    assert [r.title for r in db_session.query(models.Recipe).all()] == [
        "Nonna's Sunday Gravy"
    ]


def test_untagged_purge_without_a_user_refuses_rather_than_guessing(seeder, db_session):
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False, seed_tag=False)
    before = db_session.query(models.Recipe).count()

    removed = seeder.purge(db_session, dry_run=False, user_id=None)

    assert removed == 0
    assert db_session.query(models.Recipe).count() == before


def test_untagged_purge_leaves_another_users_rows_alone(seeder, db_session):
    """Title matching is per-user, so the other person's copy must survive."""
    seeder.seed(db_session, USER, HOUSEHOLD, dry_run=False, seed_tag=False)
    seeder.seed(db_session, "2", HOUSEHOLD, dry_run=False, seed_tag=False)

    removed = seeder.purge(db_session, dry_run=False, user_id=USER)

    assert removed == len(seeder.RECIPES)
    survivors = db_session.query(models.Recipe).all()
    assert len(survivors) == len(seeder.RECIPES)
    assert {r.user_id for r in survivors} == {"2"}
