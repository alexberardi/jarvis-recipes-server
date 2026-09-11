"""Seed a household's regular dinner rotation.

A one-off: these are the meals Alex and Kait actually cook, supplied as a list
of names, written out here with amounts and steps so they are real recipes the
planner and grocery list can work with.

Deliberate choices, so a future editor does not "fix" them back:

* Servings is 4 everywhere, and quantities follow the household's own scale --
  about four pieces of protein (two chicken breasts halved, or four steaks or
  patties) and two to three potatoes. Not per-recipe guesses.
* No brands or stores. The source list named several ("cheddar broccoli
  knorrs", "real good chicken", "Costco stuffed peppers"); those are written
  generically -- "packaged cheddar broccoli rice", "breaded chicken cutlets" --
  so the recipe stays true when the product changes or the shopper is elsewhere.
* Pantry staples (oil, salt, pepper) are listed only where a step depends on
  them, to keep the generated grocery list close to what actually gets bought.

Idempotent: keyed on (user_id, title) and skipped if present, so re-running
after adding more below is safe.

    # look before you leap -- prints what it would insert, touches nothing
    python scripts/seed_household_meals.py --user-id 1 \
        --household-id 47410f36-aec6-4b4c-a08c-05ac84db2904 --dry-run

    python scripts/seed_household_meals.py --user-id 1 \
        --household-id 47410f36-aec6-4b4c-a08c-05ac84db2904

    # removes ONLY rows this script created, via the seed tag
    python scripts/seed_household_meals.py --purge

household_id is what makes a recipe visible to the OTHER people in the house;
user_id is only authorship. Seeding without it leaves every recipe visible to
one person (services/scoping.py falls back to author-only on NULL), which for a
shared dinner rotation is the whole point missed -- so it is required unless
--no-household is passed explicitly.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from jarvis_recipes.app.db import models  # noqa: E402
from jarvis_recipes.app.db.session import SessionLocal  # noqa: E402

SEED_TAG = "seed:household-meals"
SERVINGS = 4

# (title, minutes, [(ingredient text, qty_display, qty_value, unit)], [steps], [extra tags])
RECIPES: list[tuple] = [
    (
        "Homemade Burgers and Fries", 45,
        [("ground beef", "1 1/2", 1.5, "lb"),
         ("burger buns", "4", 4.0, None),
         ("russet potatoes", "3", 3.0, None),
         ("neutral oil", "2", 2.0, "tbsp"),
         ("sliced cheese", "4", 4.0, "slices"),
         ("lettuce, tomato and onion, to serve", None, None, None)],
        ["Heat the oven to 425F. Cut the potatoes into wedges, toss with the oil, salt and pepper, and spread on a sheet pan.",
         "Roast the fries 35-40 minutes, turning once, until browned at the edges.",
         "Divide the beef into 4 patties, press a dimple into each centre, and season both sides.",
         "Cook the patties 3-4 minutes per side. Add cheese in the last minute and cover to melt.",
         "Toast the buns and build the burgers with lettuce, tomato and onion."],
        ["burgers"],
    ),
    (
        "Homemade Hamburger Helper", 30,
        [("ground beef", "1", 1.0, "lb"),
         ("elbow pasta", "8", 8.0, "oz"),
         ("beef stock", "2", 2.0, "cup"),
         ("milk", "1", 1.0, "cup"),
         ("shredded cheddar", "1 1/2", 1.5, "cup"),
         ("onion powder", "1", 1.0, "tsp"),
         ("smoked paprika", "1", 1.0, "tsp")],
        ["Brown the beef in a deep skillet over medium-high heat, breaking it up as it cooks. Drain the excess fat.",
         "Stir in the onion powder and paprika and cook 30 seconds.",
         "Add the stock, milk and dry pasta. Bring to a simmer, cover, and cook 12-14 minutes until the pasta is tender.",
         "Off the heat, stir in the cheddar until melted and the sauce coats the pasta."],
        ["one-pot"],
    ),
    (
        "Turkey Bolognese", 50,
        [("ground turkey", "1", 1.0, "lb"),
         ("crushed tomatoes", "28", 28.0, "oz"),
         ("onion, diced", "1", 1.0, None),
         ("carrots, diced", "2", 2.0, None),
         ("garlic cloves, minced", "3", 3.0, None),
         ("tomato paste", "2", 2.0, "tbsp"),
         ("pasta", "1", 1.0, "lb")],
        ["Cook the onion and carrot in oil over medium heat until softened, about 8 minutes. Add the garlic for the last minute.",
         "Add the turkey and cook until no longer pink, breaking it up as it browns.",
         "Stir in the tomato paste and cook 1 minute, then add the crushed tomatoes. Season and simmer 25-30 minutes.",
         "Boil the pasta, reserving a cup of water. Toss with the sauce, loosening with the pasta water as needed."],
        [],
    ),
    (
        "Crockpot BBQ Chicken Sandwiches", 250,
        [("boneless chicken breasts", "2", 2.0, None),
         ("bbq sauce", "1 1/2", 1.5, "cup"),
         ("onion, sliced", "1", 1.0, None),
         ("sandwich buns", "4", 4.0, None),
         ("coleslaw, to serve", None, None, None)],
        ["Put the chicken, onion and 1 cup of the bbq sauce in the slow cooker.",
         "Cook on low 4 hours, until the chicken shreds easily with a fork.",
         "Shred the chicken in the pot and stir in the remaining bbq sauce.",
         "Pile onto buns and top with coleslaw."],
        ["slow-cooker"],
    ),
    (
        "Turkey Burgers with Greek Salad", 30,
        [("ground turkey", "1 1/2", 1.5, "lb"),
         ("burger buns", "4", 4.0, None),
         ("cucumber, chopped", "1", 1.0, None),
         ("cherry tomatoes, halved", "1", 1.0, "pint"),
         ("red onion, thinly sliced", "1/2", 0.5, None),
         ("feta, crumbled", "4", 4.0, "oz"),
         ("kalamata olives", "1/2", 0.5, "cup"),
         ("olive oil and red wine vinegar, for dressing", None, None, None)],
        ["Form the turkey into 4 patties and season both sides well -- turkey needs more salt than beef.",
         "Cook 5-6 minutes per side over medium heat, until cooked through.",
         "Toss the cucumber, tomatoes, onion, olives and feta with olive oil and vinegar.",
         "Serve the burgers on toasted buns with the salad alongside."],
        ["burgers"],
    ),
    (
        "Tacos", 30,
        [("ground beef", "1", 1.0, "lb"),
         ("taco seasoning", "2", 2.0, "tbsp"),
         ("tortillas", "8", 8.0, None),
         ("shredded cheese", "1", 1.0, "cup"),
         ("lettuce, shredded", "2", 2.0, "cup"),
         ("tomato, diced", "1", 1.0, None),
         ("sour cream and salsa, to serve", None, None, None)],
        ["Brown the beef over medium-high heat, breaking it up, then drain the fat.",
         "Add the seasoning and a splash of water and simmer 5 minutes until thickened.",
         "Warm the tortillas in a dry pan.",
         "Set out the beef, cheese, lettuce, tomato, sour cream and salsa and build to taste."],
        ["quick"],
    ),
    (
        "Chicken, Mashed Potato and Corn", 45,
        [("boneless chicken breasts", "2", 2.0, None),
         ("russet potatoes", "3", 3.0, None),
         ("butter", "4", 4.0, "tbsp"),
         ("milk", "1/2", 0.5, "cup"),
         ("corn kernels", "2", 2.0, "cup"),
         ("paprika", "1", 1.0, "tsp")],
        ["Halve the breasts horizontally to make 4 thin cutlets. Season with salt, pepper and paprika.",
         "Peel and quarter the potatoes, then boil in salted water 15-18 minutes until tender.",
         "Pan-cook the chicken over medium-high heat 4-5 minutes per side until cooked through.",
         "Mash the potatoes with the butter and warm milk, and season.",
         "Warm the corn with a knob of butter and serve alongside."],
        [],
    ),
    (
        "Steak and Fries", 40,
        [("steaks, about 8 oz each", "4", 4.0, None),
         ("russet potatoes", "3", 3.0, None),
         ("neutral oil", "2", 2.0, "tbsp"),
         ("butter", "2", 2.0, "tbsp"),
         ("garlic cloves, smashed", "2", 2.0, None)],
        ["Heat the oven to 425F. Cut the potatoes into fries, toss with the oil and salt, and roast 35-40 minutes until golden and crisp.",
         "Pat the steaks dry and season generously. Let them sit at room temperature while the fries cook.",
         "Sear the steaks in a hot pan 3-4 minutes per side for medium-rare.",
         "Add the butter and garlic and spoon over the steaks for a minute, then rest 5 minutes before serving with the fries."],
        ["steak"],
    ),
    (
        "Marinated Chicken Thighs", 40,
        [("boneless chicken thighs", "4", 4.0, None),
         ("olive oil", "1/4", 0.25, "cup"),
         ("soy sauce", "2", 2.0, "tbsp"),
         ("lemon juice", "2", 2.0, "tbsp"),
         ("garlic cloves, minced", "3", 3.0, None),
         ("honey", "1", 1.0, "tbsp"),
         ("dried oregano", "1", 1.0, "tsp")],
        ["Whisk the oil, soy, lemon juice, garlic, honey and oregano together.",
         "Add the thighs, turn to coat, and marinate at least 30 minutes or overnight in the fridge.",
         "Cook over medium-high heat 6-7 minutes per side, until well browned and cooked through.",
         "Rest 5 minutes before serving."],
        ["marinade"],
    ),
    (
        "BBQ Marinated Chicken Breast", 40,
        [("boneless chicken breasts", "2", 2.0, None),
         ("bbq sauce", "3/4", 0.75, "cup"),
         ("olive oil", "2", 2.0, "tbsp"),
         ("apple cider vinegar", "1", 1.0, "tbsp"),
         ("smoked paprika", "1", 1.0, "tsp"),
         ("garlic powder", "1", 1.0, "tsp")],
        ["Halve the breasts horizontally to make 4 even cutlets so they cook through without drying.",
         "Whisk half the bbq sauce with the oil, vinegar, paprika and garlic powder, then marinate the chicken 30 minutes.",
         "Cook over medium-high heat 5-6 minutes per side.",
         "Brush with the remaining bbq sauce in the last minute so it glazes rather than burns."],
        ["marinade"],
    ),
    (
        "Turkey Burgers with Swiss and Side Salad", 30,
        [("ground turkey", "1 1/2", 1.5, "lb"),
         ("swiss cheese", "4", 4.0, "slices"),
         ("burger buns", "4", 4.0, None),
         ("mixed salad greens", "5", 5.0, "oz"),
         ("cucumber, sliced", "1", 1.0, None),
         ("vinaigrette, to dress", None, None, None)],
        ["Form 4 patties from the turkey and season both sides generously.",
         "Cook 5-6 minutes per side over medium heat, adding the swiss in the last minute to melt.",
         "Toast the buns.",
         "Dress the greens and cucumber and serve alongside."],
        ["burgers"],
    ),
    (
        "Grilled BBQ Chicken Breast with Broccoli and Cheddar Rice", 35,
        [("boneless chicken breasts", "2", 2.0, None),
         ("bbq sauce", "1/2", 0.5, "cup"),
         ("broccoli florets", "1", 1.0, "lb"),
         ("packaged cheddar broccoli rice", "1", 1.0, "pouch"),
         ("olive oil", "1", 1.0, "tbsp")],
        ["Halve the breasts horizontally to make 4 cutlets and season.",
         "Grill over medium-high heat 5-6 minutes per side, brushing with the bbq sauce near the end.",
         "Cook the rice according to its package directions.",
         "Steam or roast the broccoli with the oil until just tender, then serve everything together."],
        ["grilled"],
    ),
    (
        "Bean and Cheese Quesadillas", 20,
        [("flour tortillas, large", "4", 4.0, None),
         ("refried or black beans", "16", 16.0, "oz"),
         ("shredded cheese", "2", 2.0, "cup"),
         ("butter or oil, for the pan", None, None, None),
         ("salsa and sour cream, to serve", None, None, None)],
        ["Warm the beans and season them.",
         "Spread beans over half of each tortilla, top with cheese, and fold over.",
         "Cook in a lightly greased pan over medium heat 2-3 minutes per side, until crisp and the cheese has melted.",
         "Cut into wedges and serve with salsa and sour cream."],
        ["quick", "vegetarian"],
    ),
    (
        "Steak with Roasted Sweet Potato and Zucchini", 40,
        [("steaks, about 8 oz each", "4", 4.0, None),
         ("sweet potatoes, cubed", "2", 2.0, None),
         ("zucchini, sliced", "2", 2.0, None),
         ("olive oil", "3", 3.0, "tbsp"),
         ("garlic powder", "1", 1.0, "tsp")],
        ["Heat the oven to 425F. Toss the sweet potato with 2 tbsp oil, the garlic powder and salt, and roast 20 minutes.",
         "Add the zucchini to the pan and roast another 12-15 minutes, until both are tender and browned.",
         "Season the steaks and sear 3-4 minutes per side in the remaining oil.",
         "Rest the steaks 5 minutes, then serve with the vegetables."],
        ["steak"],
    ),
    (
        "Grilled Marinated Chicken Thighs with Vegetables and Rice", 40,
        [("boneless chicken thighs", "4", 4.0, None),
         ("olive oil", "3", 3.0, "tbsp"),
         ("lemon juice", "2", 2.0, "tbsp"),
         ("garlic cloves, minced", "3", 3.0, None),
         ("dried herbs", "1", 1.0, "tsp"),
         ("frozen mixed vegetables", "1", 1.0, "lb"),
         ("packaged rice blend", "1", 1.0, "pouch")],
        ["Marinate the thighs in the oil, lemon juice, garlic and herbs for at least 30 minutes.",
         "Grill over medium-high heat 6-7 minutes per side until cooked through.",
         "Cook the rice per its package directions.",
         "Roast or steam the frozen vegetables until hot and tender, then serve alongside."],
        ["grilled", "marinade"],
    ),
    (
        "Beef Skewers with Asparagus, Mushrooms and Rice", 40,
        [("sirloin, cut into cubes", "1 1/2", 1.5, "lb"),
         ("asparagus, trimmed", "1", 1.0, "lb"),
         ("mushrooms, halved", "8", 8.0, "oz"),
         ("olive oil", "3", 3.0, "tbsp"),
         ("soy sauce", "2", 2.0, "tbsp"),
         ("garlic cloves, minced", "2", 2.0, None),
         ("packaged rice blend", "1", 1.0, "pouch")],
        ["Toss the beef with the soy, 1 tbsp oil and the garlic, and marinate 20 minutes. Soak wooden skewers if using.",
         "Thread the beef onto skewers, threading the mushrooms between the cubes.",
         "Grill 3-4 minutes per side for medium, turning once.",
         "Toss the asparagus in the remaining oil and grill or roast 8-10 minutes until tender.",
         "Cook the rice per its package directions and serve everything together."],
        ["grilled"],
    ),
    (
        "Fish Tacos with Slaw and Lime", 30,
        [("white fish fillets", "1 1/2", 1.5, "lb"),
         ("corn or flour tortillas", "8", 8.0, None),
         ("shredded cabbage", "4", 4.0, "cup"),
         ("limes", "2", 2.0, None),
         ("mayonnaise", "1/3", 0.333, "cup"),
         ("chili powder", "1", 1.0, "tsp"),
         ("cumin", "1", 1.0, "tsp")],
        ["Toss the cabbage with the mayonnaise, the juice of one lime, salt and pepper, and set the slaw aside.",
         "Season the fish with the chili powder, cumin and salt.",
         "Cook 3-4 minutes per side over medium-high heat, until the fish flakes easily.",
         "Warm the tortillas, flake the fish into them, top with slaw and serve with the remaining lime in wedges."],
        ["quick", "seafood"],
    ),
    (
        "Spinach Stuffed Pork with Potatoes", 55,
        [("pork tenderloin or thick chops", "1 1/2", 1.5, "lb"),
         ("baby spinach", "6", 6.0, "oz"),
         ("cream cheese", "4", 4.0, "oz"),
         ("garlic cloves, minced", "2", 2.0, None),
         ("potatoes, quartered", "3", 3.0, None),
         ("olive oil", "2", 2.0, "tbsp")],
        ["Heat the oven to 400F. Toss the potatoes with 1 tbsp oil and salt and start them roasting, 35-40 minutes.",
         "Wilt the spinach with the garlic, squeeze out the liquid, and mix with the cream cheese. Season.",
         "Butterfly the pork, spread the filling inside, and tie or secure with toothpicks.",
         "Sear all over in the remaining oil, then roast 20-25 minutes until the centre reaches 145F.",
         "Rest 10 minutes before slicing, and serve with the potatoes."],
        [],
    ),
    (
        "Steak with Sweet Potato Fries and Veggie Skewers", 45,
        [("steaks, about 8 oz each", "4", 4.0, None),
         ("sweet potatoes, cut into fries", "2", 2.0, None),
         ("bell peppers, cut into chunks", "2", 2.0, None),
         ("red onion, cut into chunks", "1", 1.0, None),
         ("zucchini, thickly sliced", "1", 1.0, None),
         ("olive oil", "3", 3.0, "tbsp")],
        ["Heat the oven to 425F. Toss the sweet potato fries with 1 tbsp oil and salt and roast 30-35 minutes, turning once.",
         "Thread the peppers, onion and zucchini onto skewers and brush with the remaining oil. Season.",
         "Grill the skewers 10-12 minutes, turning, until charred and tender.",
         "Season and sear the steaks 3-4 minutes per side, then rest 5 minutes before serving."],
        ["steak", "grilled"],
    ),
    (
        "Turkey Burgers with Swiss and Sweet Potato Fries", 35,
        [("ground turkey", "1 1/2", 1.5, "lb"),
         ("swiss cheese", "4", 4.0, "slices"),
         ("burger buns", "4", 4.0, None),
         ("frozen sweet potato fries", "1", 1.0, "lb")],
        ["Cook the sweet potato fries per their package directions.",
         "Form 4 patties from the turkey and season both sides generously.",
         "Cook 5-6 minutes per side over medium heat, topping with the swiss to melt near the end.",
         "Toast the buns and serve with the fries."],
        ["burgers"],
    ),
    (
        "Stuffed Peppers", 60,
        [("bell peppers, halved and seeded", "4", 4.0, None),
         ("ground beef", "1", 1.0, "lb"),
         ("cooked rice", "2", 2.0, "cup"),
         ("tomato sauce", "15", 15.0, "oz"),
         ("onion, diced", "1", 1.0, None),
         ("shredded cheese", "1", 1.0, "cup"),
         ("italian seasoning", "1", 1.0, "tsp")],
        ["Heat the oven to 375F. Brown the beef with the onion, then drain the fat.",
         "Stir in the rice, most of the tomato sauce and the seasoning.",
         "Fill the pepper halves, set them in a baking dish, and spoon the remaining sauce over the top.",
         "Cover and bake 35 minutes, then uncover, add the cheese, and bake 10 minutes more until the peppers are tender."],
        [],
    ),
    (
        "Grilled Chicken Thighs with Pineapple and Sticky Rice", 40,
        [("boneless chicken thighs", "4", 4.0, None),
         ("pineapple, cut into rings or spears", "1/2", 0.5, None),
         ("soy sauce", "3", 3.0, "tbsp"),
         ("brown sugar", "2", 2.0, "tbsp"),
         ("garlic cloves, minced", "2", 2.0, None),
         ("fresh ginger, grated", "1", 1.0, "tsp"),
         ("short-grain white rice", "1 1/2", 1.5, "cup")],
        ["Whisk the soy, brown sugar, garlic and ginger and marinate the thighs 20 minutes.",
         "Rinse the rice until the water runs clear, then cook it according to its directions and keep it covered.",
         "Grill the thighs 6-7 minutes per side until charred and cooked through.",
         "Grill the pineapple 2-3 minutes per side, until marked and caramelised, and serve everything over the rice."],
        ["grilled"],
    ),
    (
        "Ground Beef and Sweet Potato Bowls", 35,
        [("ground beef", "1", 1.0, "lb"),
         ("frozen diced sweet potatoes", "1", 1.0, "lb"),
         ("cottage cheese", "1", 1.0, "cup"),
         ("avocados, sliced", "2", 2.0, None),
         ("olive oil", "2", 2.0, "tbsp"),
         ("smoked paprika", "1", 1.0, "tsp"),
         ("garlic powder", "1", 1.0, "tsp")],
        ["Heat the oven to 425F. Toss the sweet potatoes with the oil, paprika, garlic powder and salt, and roast 25-30 minutes.",
         "Brown the beef over medium-high heat and season well.",
         "Build the bowls with the sweet potatoes and beef.",
         "Top each with cottage cheese and sliced avocado."],
        ["bowls", "high-protein"],
    ),
    (
        "Breaded Chicken with Sheet-Pan Vegetables", 40,
        [("breaded chicken cutlets", "4", 4.0, None),
         ("broccoli florets", "1", 1.0, "lb"),
         ("bell peppers, sliced", "2", 2.0, None),
         ("red onion, sliced", "1", 1.0, None),
         ("olive oil", "2", 2.0, "tbsp"),
         ("italian seasoning", "1", 1.0, "tsp")],
        ["Heat the oven to 425F. Toss the broccoli, peppers and onion with the oil, seasoning and salt.",
         "Spread the vegetables on a sheet pan and roast 10 minutes.",
         "Add the chicken to the pan and roast 20-25 minutes more, until the chicken is cooked through and the vegetables are browned at the edges.",
         "Serve straight from the pan."],
        ["sheet-pan"],
    ),
]

# Attribution worth keeping even though the titles stay generic. "Brocc
# hamburger helper" in the source list is the broccyourbody recipe, not a
# reference to broccoli.
NOTES: dict[str, str] = {
    "Homemade Hamburger Helper": "Based on the broccyourbody version.",
}


def _get_tag(db, name: str) -> models.Tag:
    """Case-insensitive get-or-create, matching recipes_service._get_or_create_tag.

    An exact-match lookup here once created a second "dinner" alongside an
    imported "Dinner", which collapsed to one string in the mobile tag picker
    and collided as a React key.
    """
    tag = db.query(models.Tag).filter(func.lower(models.Tag.name) == name.lower()).first()
    if not tag:
        tag = models.Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def _ensure_user(db, user_id: str) -> None:
    """recipes.user_id is a FK to its own users table, which jarvis-auth does not fill.

    Every write path in the app calls this; a script that skips it hits
    ForeignKeyViolation on a database where the user has not written yet.
    """
    if db.get(models.User, user_id) is None:
        db.add(models.User(user_id=user_id))
        db.flush()


def _describe(minutes: int, title: str) -> str:
    base = f"Weeknight dinner · about {minutes} minutes · serves {SERVINGS}"
    note = NOTES.get(title)
    return f"{base}. {note}" if note else base


def seed(
    db,
    user_id: str,
    household_id: str | None,
    dry_run: bool,
    seed_tag: bool = True,
) -> tuple[int, int]:
    """Returns (created, skipped)."""
    created = skipped = 0

    if not dry_run:
        _ensure_user(db, user_id)

    for title, minutes, ingredients, steps, extra_tags in RECIPES:
        exists = (
            db.query(models.Recipe)
            .filter(models.Recipe.user_id == user_id, models.Recipe.title == title)
            .first()
        )
        if exists:
            print(f"  skip    {title}  (already present)")
            skipped += 1
            continue

        print(f"  create  {title}  [{len(ingredients)} ingredients, {len(steps)} steps]")
        created += 1
        if dry_run:
            continue

        recipe = models.Recipe(
            user_id=user_id,
            household_id=household_id,
            title=title,
            description=_describe(minutes, title),
            source_type=models.SourceType.MANUAL,
            servings=SERVINGS,
            total_time_minutes=minutes,
        )
        # Add before touching .tags: _get_tag flushes, and appending to a
        # relationship on an object the session has not seen warns ("Object of
        # type <Recipe> not in session") and silently drops the association.
        db.add(recipe)

        # The marker tag is a bookkeeping device, not something the cook wants
        # to see: it shows up in the app's tag picker next to "dinner" and
        # "steak". Off for a real household, on for dev where being able to
        # purge cleanly matters more than a tidy picker.
        if seed_tag:
            recipe.tags.append(_get_tag(db, SEED_TAG))
        recipe.tags.append(_get_tag(db, "dinner"))
        for tag_name in extra_tags:
            recipe.tags.append(_get_tag(db, tag_name))
        if minutes <= 25 and "quick" not in extra_tags:
            recipe.tags.append(_get_tag(db, "quick"))

        for text, qty_display, qty_value, unit in ingredients:
            recipe.ingredients.append(
                models.Ingredient(
                    text=text,
                    quantity_display=qty_display,
                    quantity_value=qty_value,
                    unit=unit,
                )
            )
        for n, step_text in enumerate(steps, start=1):
            recipe.steps.append(models.Step(step_number=n, text=step_text))

    # No rollback on the dry-run path: it stages nothing (the loop `continue`s
    # before any add, and _ensure_user is skipped), and rolling back a session
    # this function does not own discards the CALLER's uncommitted work.
    if not dry_run:
        db.commit()
    return created, skipped


def purge(db, dry_run: bool, user_id: str | None = None) -> int:
    """Delete only what this script created.

    Prefers the marker tag, which is exact. Rows seeded with --no-seed-tag have
    no marker, so fall back to matching this script's titles for one user --
    which needs --user-id, and is less precise: if the cook has since written
    their own "Tacos", that row matches too. Every row is printed before
    deletion for exactly that reason.
    """
    tag = db.query(models.Tag).filter(models.Tag.name == SEED_TAG).first()
    if tag and tag.recipes:
        recipes = list(tag.recipes)
    else:
        if not user_id:
            print(
                f"  nothing tagged {SEED_TAG}. If these were seeded with "
                "--no-seed-tag, pass --user-id to match them by title instead."
            )
            return 0
        titles = [r[0] for r in RECIPES]
        recipes = (
            db.query(models.Recipe)
            .filter(models.Recipe.user_id == user_id, models.Recipe.title.in_(titles))
            .all()
        )
        if recipes:
            print(
                f"  no {SEED_TAG} tag -- matching {len(recipes)} row(s) by title for "
                f"user {user_id}. Check the list below: a recipe the cook wrote "
                "themselves under one of these names would also match."
            )
    for recipe in recipes:
        print(f"  delete  {recipe.title}  (id={recipe.id}, user={recipe.user_id})")
        if not dry_run:
            db.delete(recipe)

    # Same reasoning as seed(): a dry run never calls db.delete, so there is
    # nothing to roll back.
    if not dry_run:
        db.commit()
    return len(recipes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user-id", help="Author of the recipes (recipes.user_id).")
    parser.add_argument(
        "--household-id",
        help="Household the recipes are visible to. Required unless --no-household.",
    )
    parser.add_argument(
        "--no-household",
        action="store_true",
        help="Seed with household_id NULL -- visible only to the author. Rarely what you want.",
    )
    parser.add_argument(
        "--no-seed-tag",
        action="store_true",
        help=f"Do not tag rows {SEED_TAG}. Keeps the app's tag picker clean for a "
        "real household; --purge then needs --user-id to find them by title.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing.")
    parser.add_argument("--purge", action="store_true", help=f"Delete rows tagged {SEED_TAG}.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.purge:
            print(f"PURGE{' (dry run)' if args.dry_run else ''}")
            removed = purge(db, args.dry_run, str(args.user_id) if args.user_id else None)
            print(f"\n{removed} recipe(s) {'would be ' if args.dry_run else ''}removed.")
            return 0

        if not args.user_id:
            parser.error("--user-id is required (or pass --purge)")
        if not args.household_id and not args.no_household:
            parser.error(
                "--household-id is required so the rest of the household can see these "
                "recipes. Pass --no-household to deliberately seed author-only."
            )

        print(f"SEED{' (dry run)' if args.dry_run else ''}")
        print(f"  user_id      {args.user_id}")
        print(f"  household_id {args.household_id or 'NULL (author-only)'}")
        print(f"  recipes      {len(RECIPES)}")
        print(f"  marker tag   {SEED_TAG if not args.no_seed_tag else 'none (--no-seed-tag)'}\n")

        created, skipped = seed(
            db,
            str(args.user_id),
            args.household_id,
            args.dry_run,
            seed_tag=not args.no_seed_tag,
        )
        verb = "would be created" if args.dry_run else "created"
        print(f"\n{created} {verb}, {skipped} already present.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
