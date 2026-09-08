"""Seed the dev database with realistic recipes.

Random meal-plan selection and re-roll cannot be judged against a box of one
recipe -- a broken shuffle and a working one look identical. This creates enough
variety to tell them apart: multiple meal types, cuisines, cook times and
servings, spread across the household's members so household scoping has
something real to merge rather than one user's pile.

Idempotent: recipes are keyed on (user_id, title) and skipped if present, so it
is safe to re-run after adding more below.

    python scripts/seed_dev_data.py             # seed
    python scripts/seed_dev_data.py --purge     # remove ONLY seeded recipes

Seeded rows are tagged `seed:dev` so --purge can find them without touching
anything you imported yourself.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_recipes.app.db import models  # noqa: E402
from jarvis_recipes.app.db.session import SessionLocal  # noqa: E402

SEED_TAG = "seed:dev"

# (title, meal_tag, servings, minutes, source_type, [ (text, qty_display, qty_value, unit) ], [steps])
RECIPES: list[tuple] = [
    (
        "Beef Stroganoff", "dinner", 4, 45, models.SourceType.MANUAL,
        [("beef sirloin, sliced thin", "1 1/2", 1.5, "lb"),
         ("cremini mushrooms, quartered", "8", 8.0, "oz"),
         ("yellow onion, diced", "1", 1.0, None),
         ("unsalted butter", "3", 3.0, "tbsp"),
         ("all-purpose flour", "2", 2.0, "tbsp"),
         ("beef stock", "1 1/2", 1.5, "cup"),
         ("sour cream", "3/4", 0.75, "cup"),
         ("Dijon mustard", "2", 2.0, "tsp")],
        ["Season the beef and sear in butter over high heat until browned. Remove and set aside.",
         "Cook onion and mushrooms until softened, about 8 minutes.",
         "Stir in flour, then whisk in the stock. Simmer 10 minutes until thickened.",
         "Lower heat, fold in sour cream and Dijon. Return beef and warm through. Do not boil."],
    ),
    (
        "Sheet Pan Chicken Fajitas", "dinner", 4, 35, models.SourceType.URL,
        [("boneless chicken thighs, sliced", "1 1/2", 1.5, "lb"),
         ("bell peppers, sliced", "3", 3.0, None),
         ("red onion, sliced", "1", 1.0, None),
         ("olive oil", "3", 3.0, "tbsp"),
         ("chili powder", "1", 1.0, "tbsp"),
         ("ground cumin", "2", 2.0, "tsp"),
         ("lime", "1", 1.0, None),
         ("flour tortillas", "8", 8.0, None)],
        ["Heat oven to 425F.",
         "Toss chicken and vegetables with oil and spices on a sheet pan.",
         "Roast 22-25 minutes, stirring once, until charred at the edges.",
         "Squeeze lime over the top and serve with warm tortillas."],
    ),
    (
        "Weeknight Bolognese", "dinner", 6, 60, models.SourceType.MANUAL,
        [("ground beef", "1", 1.0, "lb"),
         ("pancetta, diced", "4", 4.0, "oz"),
         ("carrot, finely diced", "1", 1.0, None),
         ("celery stalk, finely diced", "1", 1.0, None),
         ("crushed tomatoes", "28", 28.0, "oz"),
         ("whole milk", "1", 1.0, "cup"),
         ("dry red wine", "1/2", 0.5, "cup"),
         ("tagliatelle", "1", 1.0, "lb")],
        ["Render pancetta, then soften carrot, celery and onion in the fat.",
         "Add beef and brown well, breaking it up.",
         "Add wine and reduce, then milk and reduce again.",
         "Add tomatoes and simmer at least 40 minutes. Toss with pasta."],
    ),
    (
        "Lemon Garlic Salmon", "dinner", 2, 25, models.SourceType.URL,
        [("salmon fillets", "2", 2.0, None),
         ("garlic, minced", "3", 3.0, "clove"),
         ("lemon", "1", 1.0, None),
         ("olive oil", "2", 2.0, "tbsp"),
         ("fresh dill", "2", 2.0, "tbsp"),
         ("asparagus", "1", 1.0, "bunch")],
        ["Heat oven to 400F.",
         "Place salmon and asparagus on a lined sheet pan, drizzle with oil.",
         "Top with garlic, lemon slices and dill.",
         "Roast 12-15 minutes until the salmon flakes."],
    ),
    (
        "Chicken Tikka Masala", "dinner", 4, 50, models.SourceType.IMAGE,
        [("boneless chicken thighs, cubed", "2", 2.0, "lb"),
         ("plain yogurt", "1", 1.0, "cup"),
         ("garam masala", "2", 2.0, "tbsp"),
         ("crushed tomatoes", "28", 28.0, "oz"),
         ("heavy cream", "1", 1.0, "cup"),
         ("fresh ginger, grated", "1", 1.0, "tbsp"),
         ("basmati rice", "2", 2.0, "cup")],
        ["Marinate the chicken in yogurt and half the spices for 30 minutes.",
         "Sear the chicken in batches, set aside.",
         "Bloom the remaining spices, add tomatoes, simmer 15 minutes.",
         "Add cream and chicken, simmer 10 more. Serve over rice."],
    ),
    (
        "Black Bean Tacos", "dinner", 4, 20, models.SourceType.MANUAL,
        [("black beans, drained", "2", 2.0, "can"),
         ("corn tortillas", "12", 12.0, None),
         ("red cabbage, shredded", "2", 2.0, "cup"),
         ("cotija cheese, crumbled", "1/2", 0.5, "cup"),
         ("lime", "2", 2.0, None),
         ("ground cumin", "1", 1.0, "tsp")],
        ["Warm the beans with cumin and a splash of their liquid.",
         "Char the tortillas directly over a burner.",
         "Fill with beans, cabbage and cotija. Finish with lime."],
    ),
    (
        "Thai Basil Chicken", "dinner", 3, 25, models.SourceType.URL,
        [("ground chicken", "1", 1.0, "lb"),
         ("Thai basil leaves", "1", 1.0, "cup"),
         ("garlic, minced", "4", 4.0, "clove"),
         ("fish sauce", "2", 2.0, "tbsp"),
         ("oyster sauce", "1", 1.0, "tbsp"),
         ("jasmine rice", "2", 2.0, "cup"),
         ("Thai chilies", "3", 3.0, None)],
        ["Heat a wok until smoking. Fry garlic and chilies briefly.",
         "Add chicken and brown hard, breaking it up.",
         "Add sauces, toss, then kill the heat and fold in the basil.",
         "Serve over rice with a fried egg."],
    ),
    (
        "Mushroom Risotto", "dinner", 4, 45, models.SourceType.MANUAL,
        [("arborio rice", "1 1/2", 1.5, "cup"),
         ("mixed mushrooms, sliced", "1", 1.0, "lb"),
         ("chicken stock, warm", "6", 6.0, "cup"),
         ("dry white wine", "1/2", 0.5, "cup"),
         ("Parmesan, grated", "3/4", 0.75, "cup"),
         ("shallot, minced", "1", 1.0, None),
         ("unsalted butter", "4", 4.0, "tbsp")],
        ["Brown the mushrooms hard in butter, set aside.",
         "Soften the shallot, toast the rice 2 minutes.",
         "Deglaze with wine, then add stock a ladle at a time, stirring.",
         "At 18 minutes fold in mushrooms, Parmesan and remaining butter."],
    ),
    (
        "Greek Chicken Bowls", "lunch", 4, 30, models.SourceType.URL,
        [("chicken breast, cubed", "1 1/2", 1.5, "lb"),
         ("cucumber, diced", "1", 1.0, None),
         ("cherry tomatoes, halved", "1", 1.0, "pint"),
         ("feta, crumbled", "4", 4.0, "oz"),
         ("kalamata olives", "1/2", 0.5, "cup"),
         ("couscous", "1", 1.0, "cup"),
         ("lemon", "1", 1.0, None),
         ("dried oregano", "1", 1.0, "tbsp")],
        ["Toss chicken with oregano, lemon and oil; sear until cooked.",
         "Cook the couscous.",
         "Build bowls with couscous, chicken, vegetables, feta and olives."],
    ),
    (
        "Turkey Chili", "dinner", 6, 55, models.SourceType.MANUAL,
        [("ground turkey", "2", 2.0, "lb"),
         ("kidney beans", "2", 2.0, "can"),
         ("diced tomatoes", "28", 28.0, "oz"),
         ("chili powder", "3", 3.0, "tbsp"),
         ("yellow onion, diced", "1", 1.0, None),
         ("chicken stock", "2", 2.0, "cup"),
         ("tomato paste", "2", 2.0, "tbsp")],
        ["Brown the turkey with the onion.",
         "Add tomato paste and chili powder, cook 2 minutes.",
         "Add tomatoes, beans and stock. Simmer 40 minutes."],
    ),
    (
        "Cacio e Pepe", "dinner", 2, 20, models.SourceType.MANUAL,
        [("spaghetti", "8", 8.0, "oz"),
         ("Pecorino Romano, grated", "1 1/2", 1.5, "cup"),
         ("black peppercorns, cracked", "2", 2.0, "tsp"),
         ("unsalted butter", "2", 2.0, "tbsp")],
        ["Cook the pasta in well-salted water until just shy of al dente.",
         "Toast the pepper in butter in a wide pan.",
         "Add pasta and a ladle of starchy water; off heat, beat in the cheese."],
    ),
    (
        "Shakshuka", "breakfast", 3, 30, models.SourceType.URL,
        [("eggs", "6", 6.0, None),
         ("crushed tomatoes", "28", 28.0, "oz"),
         ("red bell pepper, diced", "1", 1.0, None),
         ("smoked paprika", "1", 1.0, "tbsp"),
         ("ground cumin", "1", 1.0, "tsp"),
         ("feta, crumbled", "3", 3.0, "oz"),
         ("crusty bread", "1", 1.0, "loaf")],
        ["Soften the pepper and onion, bloom the spices.",
         "Add tomatoes and simmer 15 minutes until thick.",
         "Make wells, crack in the eggs, cover and cook until just set.",
         "Scatter feta and serve with bread."],
    ),
    (
        "Overnight Oats", "breakfast", 2, 5, models.SourceType.MANUAL,
        [("rolled oats", "1", 1.0, "cup"),
         ("whole milk", "1", 1.0, "cup"),
         ("Greek yogurt", "1/2", 0.5, "cup"),
         ("maple syrup", "2", 2.0, "tbsp"),
         ("chia seeds", "1", 1.0, "tbsp"),
         ("blueberries", "1", 1.0, "cup")],
        ["Combine everything except the berries in a jar.",
         "Refrigerate overnight.",
         "Top with blueberries before serving."],
    ),
    (
        "Buttermilk Pancakes", "breakfast", 4, 25, models.SourceType.MANUAL,
        [("all-purpose flour", "2", 2.0, "cup"),
         ("buttermilk", "2", 2.0, "cup"),
         ("eggs", "2", 2.0, None),
         ("baking powder", "2", 2.0, "tsp"),
         ("granulated sugar", "2", 2.0, "tbsp"),
         ("unsalted butter, melted", "4", 4.0, "tbsp")],
        ["Whisk the dry ingredients.",
         "Whisk the wet separately, then combine until just barely mixed.",
         "Rest 10 minutes, then griddle over medium heat."],
    ),
    (
        "Tomato Soup and Grilled Cheese", "lunch", 4, 35, models.SourceType.MANUAL,
        [("canned San Marzano tomatoes", "28", 28.0, "oz"),
         ("heavy cream", "1/2", 0.5, "cup"),
         ("yellow onion, diced", "1", 1.0, None),
         ("sourdough bread", "8", 8.0, "slice"),
         ("sharp cheddar", "8", 8.0, "oz"),
         ("unsalted butter", "4", 4.0, "tbsp")],
        ["Soften the onion, add tomatoes, simmer 20 minutes.",
         "Blend smooth, then stir in the cream.",
         "Griddle the sandwiches in butter until deeply golden."],
    ),
    (
        "Chicken Caesar Salad", "lunch", 2, 20, models.SourceType.URL,
        [("romaine hearts, chopped", "2", 2.0, None),
         ("chicken breast", "1", 1.0, "lb"),
         ("Parmesan, shaved", "1/2", 0.5, "cup"),
         ("sourdough croutons", "2", 2.0, "cup"),
         ("anchovy fillets", "4", 4.0, None),
         ("lemon", "1", 1.0, None)],
        ["Grill and slice the chicken.",
         "Mash the anchovies into the dressing with lemon and Parmesan.",
         "Toss romaine with dressing, top with chicken and croutons."],
    ),
    (
        "Pork Carnitas", "dinner", 8, 210, models.SourceType.IMAGE,
        [("pork shoulder, cubed", "4", 4.0, "lb"),
         ("orange", "1", 1.0, None),
         ("white onion, quartered", "1", 1.0, None),
         ("bay leaves", "2", 2.0, None),
         ("ground cumin", "1", 1.0, "tbsp"),
         ("corn tortillas", "16", 16.0, None)],
        ["Season the pork and add to a heavy pot with onion, bay and orange halves.",
         "Cover and cook at 300F for about 3 hours until it falls apart.",
         "Shred, then crisp under the broiler before serving."],
    ),
    (
        "Veggie Stir Fry", "dinner", 3, 20, models.SourceType.MANUAL,
        [("firm tofu, cubed", "14", 14.0, "oz"),
         ("broccoli florets", "3", 3.0, "cup"),
         ("snap peas", "2", 2.0, "cup"),
         ("soy sauce", "3", 3.0, "tbsp"),
         ("toasted sesame oil", "1", 1.0, "tbsp"),
         ("fresh ginger, grated", "1", 1.0, "tbsp"),
         ("jasmine rice", "2", 2.0, "cup")],
        ["Press and sear the tofu until golden on all sides.",
         "Stir fry the vegetables hard and fast.",
         "Add sauce, toss, finish with sesame oil. Serve over rice."],
    ),
]


def _member_ids(db) -> list[str]:
    """Users to spread the seed across, so it isn't one person's pile."""
    existing = [u.user_id for u in db.query(models.User).all()]
    wanted = ["1", "2", "3"]
    for uid in wanted:
        if uid not in existing:
            db.add(models.User(user_id=uid))
    db.commit()
    return wanted


def _get_tag(db, name: str) -> models.Tag:
    # Case-insensitive, matching what the app itself does
    # (recipes_service._get_or_create_tag). An exact match here created a second
    # "dinner" alongside an imported "Dinner", which then collapsed to one string
    # in the mobile tag picker and collided as a React key.
    from sqlalchemy import func

    tag = db.query(models.Tag).filter(func.lower(models.Tag.name) == name.lower()).first()
    if not tag:
        tag = models.Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def seed(db) -> int:
    members = _member_ids(db)
    seed_tag = _get_tag(db, SEED_TAG)
    created = 0

    for i, (title, meal, servings, minutes, source, ingredients, steps) in enumerate(RECIPES):
        user_id = members[i % len(members)]
        if db.query(models.Recipe).filter(
            models.Recipe.user_id == user_id, models.Recipe.title == title
        ).first():
            continue

        recipe = models.Recipe(
            user_id=user_id,
            title=title,
            description=f"{meal.capitalize()} · about {minutes} minutes · serves {servings}",
            source_type=source,
            source_url=f"https://example.com/recipes/{title.lower().replace(' ', '-')}"
            if source == models.SourceType.URL
            else None,
            servings=servings,
            total_time_minutes=minutes,
        )
        # Add before touching .tags: _get_tag flushes, and appending to a
        # relationship on an object SQLAlchemy has not seen yet warns
        # ("Object of type <Recipe> not in session") and skips the association.
        db.add(recipe)

        recipe.tags.append(seed_tag)
        recipe.tags.append(_get_tag(db, meal))
        if minutes <= 25:
            recipe.tags.append(_get_tag(db, "quick"))

        for text, qty_display, qty_value, unit in ingredients:
            recipe.ingredients.append(
                models.Ingredient(
                    text=text, quantity_display=qty_display, quantity_value=qty_value, unit=unit
                )
            )
        for n, step_text in enumerate(steps, start=1):
            recipe.steps.append(models.Step(step_number=n, text=step_text))

        created += 1

    db.commit()
    return created


def purge(db) -> int:
    tag = db.query(models.Tag).filter(models.Tag.name == SEED_TAG).first()
    if not tag:
        return 0
    recipes = list(tag.recipes)
    for r in recipes:
        db.delete(r)
    db.commit()
    return len(recipes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purge", action="store_true", help="remove seeded recipes only")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.purge:
            print(f"removed {purge(db)} seeded recipes")
        else:
            created = seed(db)
            total = db.query(models.Recipe).count()
            print(f"created {created} recipes ({total} in the database)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
