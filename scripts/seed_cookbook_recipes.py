"""Seed recipes chosen from the Shredz Macro Masterpiece cookbook.

A one-off, like seed_household_meals.py. The 18 recipes below were picked from
the 209 in the book for being genuinely simple (few steps) and close to what
this household already cooks: beef-and-potato bowls, air-fryer breaded chicken,
and Korean/teriyaki-leaning dishes.

How the data got here, and why it is inlined rather than parsed at runtime:

* The PDF has a real text layer, so this needed parsing, not OCR. The pages are
  two-column (ingredients left, instructions right); pdftotext without -layout
  interleaves them into an unusable stream, and the right column's step numbers
  start a character or two left of the INSTRUCTIONS heading, so the column seam
  has to be measured from where numbered steps begin.
* Inlining the result keeps prod independent of a 17MB PDF that is not on the
  server, and makes the recipes reviewable in a diff.
* Amounts are split into quantity/unit where the source made that unambiguous
  (252 of 286 ingredients). The rest -- "Salt & pepper, to taste", "Olive oil
  spray" -- carry no amount on purpose rather than a fabricated one.
* Brand plugs are stripped, matching the rule used for the hand-written meals:
  the cookbook sells a flour, and each mention already named oat flour as the
  alternative.
* Servings are left as the book has them. Most are 4; the chili is 10, which is
  deliberate -- leftovers get frozen.

Recipes are tagged "Alex" (his pick) plus "dinner".

    python scripts/seed_cookbook_recipes.py --user-id 1 \
        --household-id <household> --dry-run
    python scripts/seed_cookbook_recipes.py --user-id 1 --household-id <household>
    python scripts/seed_cookbook_recipes.py --purge --user-id 1

--purge matches this script's titles for one user. It deliberately does NOT key
on the "Alex" tag: that is a real tag the household uses, so purging by it would
delete recipes nobody asked to lose.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from jarvis_recipes.app.db import models  # noqa: E402
from jarvis_recipes.app.db.session import SessionLocal  # noqa: E402

SOURCE = "Shredz Macro Masterpiece"
PICK_TAG = "Alex"

# (text, quantity_display, quantity_value, unit) per ingredient.
RECIPES: list[dict] = [
    {
        "title": "Lemon Garlic Chicken with Broccoli Cheddar Rice",
        "page": 70,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("boneless skinless chicken breast, cut into bite- sized cubes", "24", 24.0, "oz"),
            ("olive oil", "2", 2.0, "tbsp"),
            ("Juice of 1 whole lemon (50 ml)", None, None, None),
            ("Zest from half a lemon (2g)", None, None, None),
            ("garlic cloves, minced (9g)", "3", 3.0, None),
            ("dried parsley", "2", 2.0, "tsp"),
            ("Salt and pepper to taste", None, None, None),
            ("jasmine rice", "1", 1.0, "cup"),
            ("chicken broth", "2", 2.0, "cups"),
            ("butter", "1", 1.0, "tbsp"),
            ("broccoli, steamed and chopped", "2", 2.0, "cups"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("sharp cheddar cheese, shredded", "1/2", 0.5, "cup"),
        ],
        "steps": [
            "Place chicken cubes in a ziplock bag with olive oil, lemon juice, lemon zest, minced garlic, dried parsley, salt, and pepper. Seal and massage to coat. Marinate for at least 30 minutes, up to 2 hours.",
            "Combine jasmine rice and chicken broth in a saucepan. Cover and cook on low for about 18 minutes, or until rice is tender and liquid is absorbed. Remove from heat and add butter, steamed broccoli, garlic powder, and cheddar cheese. Stir to combine.",
            "While rice is cooking, heat a skillet over medium-high heat. Add oil if needed, then add marinated chicken. Cook for 8-10 minutes, stirring occasionally, until chicken is cooked through and reaches 165°F (75°C). Remove from heat and let rest.",
            "Divide rice among four meal prep containers. Top each with cooked chicken.",
        ],
    },
    {
        "title": "Garlic Herb Steak & Parmesan Potato Bowls",
        "page": 141,
        "serving": 4,
        "minutes": 40,
        "ingredients": [
            ("russet potatoes, diced", "3", 3.0, "medium"),
            ("olive oil", "1", 1.0, "tablespoon"),
            ("garlic powder", "1", 1.0, "teaspoon"),
            ("Salt and pepper, to taste", None, None, None),
            ("grated Parmesan", "2", 2.0, "tablespoons"),
            ("lean sirloin or flank steak, diced", "567", 567.0, "g"),
            ("Salt, pepper, garlic powder, to taste", None, None, None),
            ("olive oil, for searing", "1/2", 0.5, "tablespoon"),
            ("yellow onion, thinly sliced", "1", 1.0, "medium"),
            ("olive oil or cooking spray", "1", 1.0, "teaspoon"),
            ("Worcestershire sauce", None, None, None),
            ("non-fat Greek yogurt", "1/2", 0.5, "cup"),
            ("low-fat cottage cheese", "1/4", 0.25, "cup"),
            ("lemon juice", "1", 1.0, "tablespoon"),
            ("chopped fresh parsley", "1", 1.0, "tablespoon"),
            ("chopped chives or green onion", "1", 1.0, "tablespoon"),
            ("Salt, to taste", None, None, None),
        ],
        "steps": [
            "Preheat the oven to 425°F (220°C). Toss diced potatoes with olive oil, garlic powder, salt, and pepper. Spread on a baking sheet and roast for 25–30 minutes, flipping halfway through. In the last 5 minutes, sprinkle with Parmesan and return to oven until golden and crisp.",
            "While the potatoes are roasting, heat a skillet over medium- high heat with ½ tbsp olive oil. Season the diced steak with salt, pepper, and garlic powder. Sear for 6–8 minutes, or until cooked to your liking. Set aside to rest.",
            "In the same skillet, reduce heat to medium. Add sliced onions and olive oil or spray. Cook for 5–7 minutes until caramelized. Add a splash of balsamic vinegar or Worcestershire if desired.",
            "Blend together all creamy herb sauce ingredients until smooth. Adjust seasoning as needed.",
            "Assemble each bowl with roasted potatoes, sautéed onions, and steak. Drizzle with creamy herb sauce and garnish with extra herbs if desired.",
        ],
    },
    {
        "title": "Spicy Korean Beef Bowl",
        "page": 152,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("93/7 lean ground beef", "1", 1.0, "lb"),
            ("low-sodium soy sauce", "2", 2.0, "tbsp"),
            ("gochujang (Korean chili paste)", "1", 1.0, "tbsp"),
            ("honey", "1", 1.0, "tbsp"),
            ("rice vinegar", "1", 1.0, "tbsp"),
            ("minced garlic", "1", 1.0, "tbsp"),
            ("sesame oil", "1", 1.0, "tsp"),
            ("Salt & pepper to taste", None, None, None),
            ("chopped green onion (for topping)", "1", 1.0, "tbsp"),
            ("cooked or 1 cup uncooked jasmine or brown rice", "2", 2.0, "cups"),
            ("cucumber, chopped", "1", 1.0, "large"),
            ("cherry tomatoes, halved", "1/2", 0.5, "cup"),
            ("shallot, finely chopped", "1", 1.0, "small"),
            ("balsamic vinegar", "1", 1.0, "tbsp"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("chopped fresh basil", "1", 1.0, "tbsp"),
            ("chopped fresh oregano", "1", 1.0, "tsp"),
            ("Salt & pepper, to taste", None, None, None),
            ("Sesame seeds", None, None, None),
            ("Sriracha drizzle or spicy mayo (Greek yogurt base)", None, None, None),
        ],
        "steps": [
            "In a skillet over medium heat, cook ground beef until fully browned. Drain excess fat if needed.",
            "Add soy sauce, gochujang, honey, rice vinegar, garlic, sesame oil, salt, and pepper. Stir well and simmer for 2–3 minutes until thickened.",
            "In a bowl, combine cucumber, cherry tomatoes, shallot, vinegar, olive oil, basil, oregano, salt, and pepper. Mix and set aside.",
            "Assemble each bowl with ½ cup cooked rice, beef mixture, and cucumber salad. Top with green onions, sesame seeds, and optional sauce.",
        ],
    },
    {
        "title": "Mexican Street Corn Chicken Bowl",
        "page": 147,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("boneless, skinless chicken breast", "500", 500.0, "g"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("chili powder", "1", 1.0, "tsp"),
            ("smoked paprika", "1/2", 0.5, "tsp"),
            ("cumin", "1/2", 0.5, "tsp"),
            ("garlic powder", "1/2", 0.5, "tsp"),
            ("onion powder", "1/2", 0.5, "tsp"),
            ("salt", "1/2", 0.5, "tsp"),
            ("black pepper", "1/4", 0.25, "tsp"),
            ("Juice of 1 lime", None, None, None),
            ("corn kernels (fresh, canned, or frozen)", "2", 2.0, "cups"),
            ("butter", "1", 1.0, "tbsp"),
            ("garlic clove, minced", "1", 1.0, None),
            ("light mayo", "1/4", 0.25, "cup"),
            ("non-fat Greek yogurt", "1/4", 0.25, "cup"),
            ("crumbled cotija cheese (or feta)", "1/4", 0.25, "cup"),
            ("fresh cilantro, chopped", "1/4", 0.25, "cup"),
            ("jasmine or basmati rice", "1", 1.0, "cup"),
            ("water or chicken broth", "2", 2.0, "cups"),
        ],
        "steps": [
            "In a pot, combine rice, water or broth, lime juice, salt, and bring to a boil. Cover, reduce heat, and simmer for 15 minutes. Once cooked, fluff with a fork and mix in chopped cilantro.",
            "Dice chicken breast and season with olive oil, spices, and lime juice. Sauté in a hot pan over medium heat for 8–10 minutes, or until fully cooked and slightly charred. Set aside.",
            "In a skillet, melt butter and sauté garlic for 1 minute. Add corn and cook until slightly charred, about 5–7 minutes. Remove from heat and mix in mayo, Greek yogurt, cotija cheese, chili powder, lime juice, and chopped cilantro.",
            "Divide rice, chicken, and street corn into four bowls. Top with extra cheese or herbs if desired.",
        ],
    },
    {
        "title": "Crockpot Chili",
        "page": 45,
        "serving": 10,
        "minutes": 40,
        "ingredients": [
            ("lean 93/7 ground beef", "2", 2.0, "lb"),
            ("onion, diced (225)", "1", 1.0, "large"),
            ("garlic, minced", "3", 3.0, "cloves"),
            ("cumin powder", "2", 2.0, "tsp"),
            ("chili powder", "2", 2.0, "tbsp"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("dried oregano", "1", 1.0, "tsp"),
            ("salt, or to taste", "1 1/2", 1.5, "tsp"),
            ("black pepper", "1/2", 0.5, "tsp"),
            ("canned black beans, drained and rinsed", "15", 15.0, "oz"),
            ("kidney beans, drained and rinsed", "30", 30.0, "oz"),
            ("diced tomatoes, with their juice", "30", 30.0, "oz"),
            ("diced tomatoes and green chilis, with their juice", "10", 10.0, "oz"),
            ("tomato sauce", "30", 30.0, "oz"),
        ],
        "steps": [
            "Place a large skillet over medium-high heat and sauté beef until it releases fat (4-5 minutes), breaking it up with a spatula.",
            "Add onion to the skillet and sauté until tender (4-5 minutes). Add minced garlic and seasonings: cumin, chili powder, garlic powder, dried oregano, salt and pepper. Cook for another 30 seconds stirring constantly. Transfer to a 6 Qt slow cooker.",
            "Add remaining ingredients into the slow cooker: rinsed and drained beans, diced tomatoes with their juice, diced tomatoes and green chilis with juice and tomato sauce. Stir to combine and cook on high for 3-4 hours or on low for 6-8 hours. Season to taste if desired and serve warm.",
        ],
    },
    {
        "title": "Fiesta Beef & Potato Bowl",
        "page": 181,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("russet potatoes, cubed (about 4 medium)", "600", 600.0, "g"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("smoked paprika", "1", 1.0, "tsp"),
            ("Salt & pepper, to taste", None, None, None),
            ("Olive oil spray", None, None, None),
            ("lean ground beef (93/7 or leaner)", "1 1/2", 1.5, "lb"),
            ("onion, diced", "1", 1.0, "small"),
            ("low-sodium taco seasoning (or homemade)", "1", 1.0, "packet"),
            ("beef bone broth", "1/4", 0.25, "cup"),
            ("fresh guacamole", "1/2", 0.5, "cup"),
            ("pico de gallo", "1/2", 0.5, "cup"),
            ("Pickled jalapeños (optional)", None, None, None),
            ("non-fat Greek yogurt", "1/2", 0.5, "cup"),
            ("chipotle pepper in adobo + 1 tsp adobo sauce", "1", 1.0, None),
            ("roasted red bell pepper (or 2 jarred pieces)", "1/2", 0.5, None),
            ("–2 tbsp lime juice", "1", 1.0, None),
            ("honey (or agave)", "1/2", 0.5, "tsp"),
            ("Handful fresh cilantro", None, None, None),
            ("Pinch of salt", None, None, None),
        ],
        "steps": [
            "Preheat oven 425°F or air fryer 400°F. Toss potatoes with garlic powder, smoked paprika, salt, pepper, and oil spray. Roast or air fry for 20 minutes until crispy and golden.",
            "In a skillet, brown ground beef with diced onion. Stir in taco seasoning and beef bone broth. Simmer for 3–4 minutes until thickened.",
            "Build bowls starting with crispy potatoes, followed by seasoned beef, guacamole, and pico de gallo. Add jalapeños if desired.",
            "Drizzle with Fiesta Sauce and serve hot.",
        ],
    },
    {
        "title": "Spicy Garlic Beef & Sweet Potato Bowl",
        "page": 186,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("sweet potatoes, cubed (about 2 large)", "600", 600.0, "g"),
            ("smoked paprika", "1", 1.0, "tsp"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("chili powder", "1", 1.0, "tsp"),
            ("Salt & pepper, to taste", None, None, None),
            ("Olive oil spray", None, None, None),
            ("93/7 lean ground beef", "1 1/2", 1.5, "lb"),
            ("white onion, diced", "1/2", 0.5, "large"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("Worcestershire or low-sodium soy sauce", "1", 1.0, "tbsp"),
            ("garlic, minced", "2", 2.0, "cloves"),
            ("cumin", "1", 1.0, "tsp"),
            ("onion powder", "1", 1.0, "tsp"),
            ("red pepper flakes (optional)", "1/2", 0.5, "tsp"),
            ("Salt & black pepper, to taste", None, None, None),
            ("Juice of ½ lime", None, None, None),
            ("nonfat Greek yogurt", "1/2", 0.5, "cup"),
            ("light mayo", "2", 2.0, "tbsp"),
            ("lime juice", "1", 1.0, "tbsp"),
            ("chipotle pepper sauce", "1", 1.0, "tsp"),
            ("honey (optional, balances heat)", "1", 1.0, "tsp"),
            ("Pinch of garlic powder", None, None, None),
            ("Fresh pico de gallo (store-bought or homemade)", None, None, None),
            ("Fresh chopped cilantro", None, None, None),
        ],
        "steps": [
            "Roast the Potatoes: Preheat oven to 425°F (or air fryer to 400°F). Toss cubed sweet potatoes with smoked paprika, garlic powder, chili powder, salt, pepper, and olive oil spray. Roast or air fry 20–25 minutes until golden and crispy.",
            "Cook the Beef: In a skillet, heat olive oil over medium-high. Add diced onion and cook until softened. Stir in garlic, then add ground beef. Break apart and cook until browned. Add Worcestershire, lime juice, and seasonings. Simmer 3–4 minutes until well combined.",
            "Make the Sauce: In a small bowl, whisk Greek yogurt, light mayo, lime juice, chipotle pepper sauce, honey, garlic powder, salt, and pepper until smooth. Adjust seasoning to taste.",
            "Assemble Bowls: Divide roasted sweet potatoes into bowls. Top with spicy garlic beef, fresh pico de gallo, and cilantro. Drizzle with creamy sauce and serve hot.",
        ],
    },
    {
        "title": "Garlic Butter Beef & Potato Bowl",
        "page": 154,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("lean ground beef (93/7 or 96/4)", "1", 1.0, "lb"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("butter (optional, for extra flavor)", "1", 1.0, "tbsp"),
            ("low-sodium soy sauce", "1", 1.0, "tbsp"),
            ("Worcestershire sauce", "1", 1.0, "tbsp"),
            ("minced garlic", "1", 1.0, "tbsp"),
            ("smoked paprika", "1", 1.0, "tsp"),
            ("onion powder", "1", 1.0, "tsp"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("Salt & pepper, to taste", None, None, None),
            ("russet potatoes (400g), cubed", "2", 2.0, "medium"),
            ("paprika", "1/2", 0.5, "tsp"),
            ("salt", "1/2", 0.5, "tsp"),
            ("non-fat Greek yogurt", "1/2", 0.5, "cup"),
            ("light mayo", "1", 1.0, "tbsp"),
            ("lemon juice", "1", 1.0, "tbsp"),
            ("dried dill or parsley", "1", 1.0, "tsp"),
            ("chopped green onions", "1", 1.0, "tbsp"),
            ("Salt, to taste", None, None, None),
            ("Parmesan cheese", "1", 1.0, "tbsp"),
        ],
        "steps": [
            "Roast the Potatoes: Preheat oven to 425°F (220°C). Toss cubed potatoes with olive oil, garlic powder, paprika, and salt. Spread on a baking sheet and roast for 25–30 minutes or until golden and crispy, flipping halfway through.",
            "Cook the Beef: While the potatoes roast, heat olive oil in a skillet over medium heat. Add ground beef and cook until browned. Drain excess fat if needed.",
            "Add butter (if using), minced garlic, soy sauce, Worcestershire sauce, and remaining seasonings to the skillet. Stir well and simmer for 2–3 minutes to absorb the flavors.",
            "Make the Herb Drizzle: In a bowl, combine Greek yogurt, mayo, lemon juice, garlic powder, dill or parsley, green onions, and salt. Mix until smooth and creamy.",
            "Assemble Bowls: Divide roasted potatoes into 4 bowls. Top with garlic butter beef, drizzle with creamy herb sauce, and garnish with green onions and Parmesan if desired.",
        ],
    },
    {
        "title": "Tex-Mex Beef & Sweet Potato Bowl",
        "page": 138,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("Base", None, None, None),
            ("sweet potatoes (600g total), cubed", "2", 2.0, "large"),
            ("Light cooking spray (1 tsp total)", None, None, None),
            ("red onion (75g), diced", "1/2", 0.5, None),
            ("lean ground beef (93/7)", "1", 1.0, "lb"),
            ("garlic cloves, minced", "3", 3.0, None),
            ("chicken broth", "1/4", 0.25, "cup"),
            ("fire-roasted diced tomatoes", "1/4", 0.25, "cup"),
            ("shredded pepper jack cheese", "1/2", 0.5, "cup"),
            ("chili powder", "1", 1.0, "tsp"),
            ("smoked paprika", "1", 1.0, "tsp"),
            ("ground cinnamon", "1/2", 0.5, "tsp"),
            ("salt", "1", 1.0, "tsp"),
            ("non-fat Greek yogurt", "1/2", 0.5, "cup"),
            ("lime juice", "1", 1.0, "tbsp"),
            ("honey", "1", 1.0, "tsp"),
            ("adobo sauce (from canned chipotles)", "1", 1.0, "tsp"),
            ("Salt & pepper to taste", None, None, None),
        ],
        "steps": [
            "Preheat oven to 425°F (220°C). Toss cubed sweet potatoes with light cooking spray and a pinch of salt. Roast for 20–25 minutes or until tender and golden.",
            "While potatoes are roasting, heat a skillet over medium heat. Sauté diced onion and garlic until fragrant, about 2–3 minutes.",
            "Add ground beef and cook until browned. Drain excess fat if needed.",
            "Stir in chicken broth, diced tomatoes, chili powder, smoked paprika, cinnamon, and salt. Let simmer for 5–7 minutes until flavors meld.",
            "In a small bowl, whisk together all chipotle yogurt sauce ingredients. Add water as needed to thin to desired consistency.",
            "Assemble bowls by dividing roasted sweet potatoes and Tex- Mex beef mixture into four servings. Top each with shredded pepper jack cheese and drizzle with chipotle yogurt sauce.",
        ],
    },
    {
        "title": "Sweet Potato & Beef High-Protein Bowl",
        "page": 137,
        "serving": 4,
        "minutes": 20,
        "ingredients": [
            ("sweet potatoes (approx. 600g), cubed", "2", 2.0, "large"),
            ("Light cooking spray (about 1 tsp total)", None, None, None),
            ("white onion (75g), diced", "1/2", 0.5, None),
            ("lean ground beef (93/7)", "1", 1.0, "lb"),
            ("garlic cloves, minced", "3", 3.0, None),
            ("tomato paste", "1/4", 0.25, "cup"),
            ("shredded reduced-fat cheddar cheese", "1/2", 0.5, "cup"),
            ("cumin", "1", 1.0, "tsp"),
            ("paprika", "1", 1.0, "tsp"),
            ("cayenne pepper (adjust to spice preference)", "1", 1.0, "tsp"),
            ("salt", "1", 1.0, "tsp"),
            ("non-fat Greek yogurt (or dairy- free alternative)", "1/2", 0.5, "cup"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("lemon juice", "1", 1.0, "tbsp"),
            ("fresh parsley, chopped", "1", 1.0, "tbsp"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("onion powder", "1", 1.0, "tsp"),
            ("Dijon mustard", "1", 1.0, "tsp"),
            ("Salt & pepper, to taste", None, None, None),
            ("Water, as needed to thin", None, None, None),
        ],
        "steps": [
            "Preheat oven to 425°F (220°C). Toss cubed sweet potatoes with cooking spray, season with salt, and roast on a baking sheet for 20–25 minutes or until tender and lightly crispy.",
            "While the potatoes are roasting, heat a skillet over medium heat. Add diced onion and garlic; sauté until softened.",
            "Add ground beef, breaking it up as it cooks. Once browned, stir in tomato paste, cumin, paprika, cayenne, and salt. Simmer for 2–3 minutes to combine flavors.",
            "While the beef cooks, whisk together all garlic herb sauce ingredients in a small bowl. Add water as needed to thin to your desired consistency.",
            "Once sweet potatoes are done, assemble bowls by dividing them evenly, then topping with the seasoned beef, shredded cheese, and a drizzle of garlic herb sauce. Garnish with extra parsley if desired.",
        ],
    },
    {
        "title": "Korean BBQ Chicken Tenders",
        "page": 144,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("boneless, skinless chicken tenders", "567", 567.0, "g"),
            ("egg", "1", 1.0, "large"),
            ("cornflakes, crushed", "1.5", 1.5, "cup"),
            ("oat flour", "1/4", 0.25, "cup"),
            ("garlic powder", "1", 1.0, "teaspoon"),
            ("Salt and pepper, to taste", None, None, None),
            ("low-sugar ketchup", "1/4", 0.25, "cup"),
            ("low-sodium soy sauce", "2", 2.0, "tablespoons"),
            ("gochujang (Korean red chili paste)", "2", 2.0, "tablespoons"),
            ("honey", "1", 1.0, "tablespoon"),
            ("rice vinegar", "1", 1.0, "tablespoon"),
            ("sesame oil", "1", 1.0, "teaspoon"),
            ("minced garlic", "1", 1.0, "teaspoon"),
        ],
        "steps": [
            "Preheat oven or air fryer to 400°F (200°C).",
            "Set up a breading station: coat chicken tenders lightly in flour, dip in beaten egg, then press into crushed cornflakes mixed with garlic powder, salt, and pepper.",
            "Place coated chicken tenders on a lined baking sheet or in the air fryer basket. Spray lightly with oil and bake/air fry for 15–18 minutes, flipping halfway through, until golden and cooked through.",
            "While the chicken cooks, mix all sauce ingredients in a small saucepan over medium heat. Simmer for 3–5 minutes until slightly thickened.",
            "Toss cooked tenders in sauce or serve sauce on the side for dipping.",
        ],
    },
    {
        "title": "Hot Honey Chicken Tenders",
        "page": 165,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("chicken tenderloins (approx. 7–8 tenders)", "1.5", 1.5, "lbs"),
            ("Salt, pepper, garlic powder, onion powder, paprika to taste", None, None, None),
            ("oat flour", "1/2", 0.5, "cup"),
            ("eggs, whisked with 1 tbsp water", "2", 2.0, "large"),
            ("crushed cornflakes", "1.5", 1.5, "cups"),
            ("Spray oil (avocado or olive oil)", None, None, None),
            ("hot sauce (Buffalo sauce)", "1/4", 0.25, "cup"),
            ("honey (or sugar-free honey)", "2", 2.0, "tbsp"),
            ("butter", "1", 1.0, "tbsp"),
            ("apple cider vinegar", "1", 1.0, "tbsp"),
            ("garlic powder", "1/2", 0.5, "tsp"),
        ],
        "steps": [
            "Preheat air fryer or oven to 400°F (200°C).",
            "Season chicken tenderloins with salt, pepper, garlic powder, onion powder, and paprika on all sides.",
            "Set up a breading station: place oat flour in one bowl, whisked eggs in another, and crushed cornflakes in a third.",
            "Dredge each tender first in flour, then dip in egg wash, then coat thoroughly in crushed cornflakes.",
            "Place breaded tenders in a single layer in the air fryer basket or on a lined baking sheet. Lightly spray with oil for crispiness.",
            "Air fry for 12–15 minutes (or bake for 18–20 minutes), flipping halfway, until golden brown and cooked through.",
            "While the tenders cook, make the sauce: add Buffalo sauce, honey, butter, apple cider vinegar, and garlic powder to a small saucepan over medium heat. Stir until butter melts and sauce is well combined.",
            "Once cooked, drizzle hot honey sauce over chicken tenders or toss to coat evenly. Serve immediately with optional extra sauce for dipping.",
        ],
    },
    {
        "title": "High-Protein Nashville Hot Chicken Tenders",
        "page": 175,
        "serving": 4,
        "minutes": 20,
        "ingredients": [
            ("chicken tenderloins", "1.5", 1.5, "lb"),
            ("non-fat Greek yogurt", "1/2", 0.5, "cup"),
            ("hot sauce (like Frank’s or Crystal)", "1", 1.0, "tbsp"),
            ("pickle juice", "1", 1.0, "tbsp"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("smoked paprika", "1", 1.0, "tsp"),
            ("salt", "1/2", 0.5, "tsp"),
            ("black pepper", "1/2", 0.5, "tsp"),
            ("crushed cornflakes or panko breadcrumbs", "2", 2.0, "cups"),
            ("olive oil", "1", 1.0, "tbsp"),
            ("Hot sauce, to taste", None, None, None),
            ("honey", "1", 1.0, "tsp"),
            ("cayenne pepper", "1", 1.0, "tsp"),
            ("ranch seasoning", "1", 1.0, "tsp"),
            ("honey or sugar-free syrup", "1", 1.0, "tsp"),
            ("Splash of pickle juice or lemon juice, to taste", None, None, None),
        ],
        "steps": [
            "In a large bowl, combine chicken tenderloins with Greek yogurt, hot sauce, pickle juice, garlic powder, smoked paprika, salt, and pepper. Mix until coated. Marinate for at least 30 minutes or overnight for maximum flavor.",
            "Preheat air fryer to 400°F (200°C) or oven to 425°F (218°C).",
            "Coat chicken pieces evenly in crushed cornflakes or panko. Lightly spray with olive oil.",
            "Air fry for 10–12 minutes (or bake for 15–18 minutes), flipping halfway, until golden and fully cooked (internal temp 165°F/74°C).",
            "In a small bowl, mix Nashville Hot Oil ingredients. Brush over cooked chicken.",
            "For the dipping sauce, mix Greek yogurt, ranch seasoning, honey (or syrup), and pickle/lemon juice until smooth.",
            "Serve tenders hot with dipping sauce on the side.",
        ],
    },
    {
        "title": "High Protein Chicken Fries (Air Fryer)",
        "page": 159,
        "serving": 4,
        "minutes": 60,
        "ingredients": [
            ("chicken breast, cut into fry-sized strips", "1.5", 1.5, "lbs"),
            ("eggs", "2", 2.0, "large"),
            ("water", "1", 1.0, "tbsp"),
            ("oat flour", "1/2", 0.5, "cup"),
            ("crushed cornflakes", "1.5", 1.5, "cups"),
            ("grated Parmesan cheese", "1/2", 0.5, "cup"),
            ("garlic powder", "1", 1.0, "tsp"),
            ("onion powder", "1", 1.0, "tsp"),
            ("paprika", "1/2", 0.5, "tsp"),
            ("salt", "1/2", 0.5, "tsp"),
            ("Spray oil (e.g. avocado oil spray)", None, None, None),
        ],
        "steps": [
            "Preheat air fryer to 400°F (205°C).",
            "Set up three stations: one bowl with whisked eggs and water, one with flour, and one with crushed cornflakes, Parmesan, and seasonings mixed together.",
            "Dredge each chicken strip in flour, dip in the egg wash, then coat fully in the cornflake mixture.",
            "Spray the air fryer basket with oil and place chicken fries in a single layer (cook in batches if needed). Spray the tops lightly with oil.",
            "Air fry for 12–15 minutes, flipping halfway through, until golden and cooked through.",
        ],
    },
    {
        "title": "Sticky Garlic Chicken Noodles",
        "page": 223,
        "serving": 4,
        "minutes": 30,
        "ingredients": [
            ("boneless, skinless chicken breast", "1 1/2", 1.5, "lbs"),
            ("Salt & black pepper, to taste", None, None, None),
            ("Garlic powder, to taste", None, None, None),
            ("Oil spray", None, None, None),
            ("dry egg or lo mein noodles", "8", 8.0, "oz"),
            ("low-sodium soy sauce", "1/4", 0.25, "cup"),
            ("hoisin sauce (32g)", "2", 2.0, "tbsp"),
            ("honey (31.5g)", "1 1/2", 1.5, "tbsp"),
            ("rice vinegar", "1", 1.0, "tbsp"),
            ("cornstarch (8g)", "1", 1.0, "tbsp"),
            ("beef stock", "1/2", 0.5, "cup"),
            ("sriracha (optional)", "1", 1.0, "tbsp"),
            ("garlic, minced (18g)", "6", 6.0, "cloves"),
            ("fresh grated ginger (5g)", "1", 1.0, "tsp"),
            ("Green onions, for topping", None, None, None),
        ],
        "steps": [
            "Cook the Noodles: Cook noodles according to package directions. Drain and set aside.",
            "Cook the Chicken: Season chicken with salt, pepper, and garlic powder. Sear in a hot skillet with oil spray until golden and cooked through. Remove from pan.",
            "Sauté Aromatics: Lower heat slightly and add garlic and ginger. Cook 10–15 seconds until fragrant.",
            "Make the Sauce: Whisk sauce ingredients together, pour into the pan, and simmer until thick and glossy.",
            "Combine: Add noodles and chicken back to the pan. Toss until everything is evenly coated in sauce.",
            "Serve: Enjoy hot topped with green onions, letting it rest 30 seconds off heat so the sauce tightens and gets extra sticky.",
        ],
    },
    {
        "title": "Korean Ground Beef Bowl",
        "page": 200,
        "serving": 4,
        "minutes": 20,
        "ingredients": [
            ("extra-lean ground beef (96/4)", "1", 1.0, "lb"),
            ("minced garlic (about cloves)", "1 1/2", 1.5, "tbsp"),
            ("grated fresh ginger", "1", 1.0, "tbsp"),
            ("low-sodium soy sauce", "1/4", 0.25, "cup"),
            ("honey", "2", 2.0, "tbsp"),
            ("rice vinegar", "1", 1.0, "tbsp"),
            ("gochujang (Korean chili paste)", "1", 1.0, "tbsp"),
            ("toasted sesame oil", "1", 1.0, "tsp"),
            ("green onions, thinly sliced (reserve some for topping)", "3", 3.0, None),
            ("cooked jasmine or white rice", "2", 2.0, "cups"),
            ("eggs (1 per bowl)", "4", 4.0, None),
            ("cucumber, sliced", "1", 1.0, "large"),
            ("shredded carrots or pickled onions (optional)", "1", 1.0, "cup"),
            ("shredded lettuce or cabbage (optional, for extra veggies)", "1", 1.0, "cup"),
            ("Sesame seeds, for garnish", None, None, None),
        ],
        "steps": [
            "Cook the Beef: In a large skillet over medium-high heat, cook the ground beef until browned. Drain excess fat if needed.",
            "Add Aromatics: Stir in garlic and ginger, cooking 1 minute until fragrant.",
            "Make the Sauce: Add soy sauce, honey, rice vinegar, gochujang, and sesame oil. Stir and simmer 2–3 minutes until the sauce thickens and becomes glossy. Mix in most of the green onions.",
            "Assemble Bowls: Divide cooked rice among 4 bowls. Top each with beef mixture and a sunny-side-up egg.",
            "Add Toppings: Finish with cucumber slices, carrots, lettuce or cabbage, sesame seeds, and extra green onions. 💡 Pro Tip: Mix a little gochujang with soy sauce and drizzle over the egg yolk for that glossy, restaurant-style finish.",
        ],
    },
    {
        "title": "Egg Roll in a Bowl (One Pot)",
        "page": 222,
        "serving": 4,
        "minutes": 20,
        "ingredients": [
            ("93/7 lean ground turkey", "1 1/2", 1.5, "lbs"),
            ("sesame oil", "1", 1.0, "tbsp"),
            ("white onion, diced (70g)", "1", 1.0, "small"),
            ("garlic, minced (12g)", "4", 4.0, "cloves"),
            ("fresh grated ginger (6g)", "1", 1.0, "tbsp"),
            ("Salt, black pepper, and garlic powder, to taste", None, None, None),
            ("–16 oz (425g) coleslaw mix", "14", 14.0, None),
            ("green onions, sliced (30g)", "3", 3.0, None),
            ("low-sodium soy sauce or coconut aminos", "1/4", 0.25, "cup"),
            ("rice vinegar", "1", 1.0, "tbsp"),
            ("hoisin sauce (16g)", "1", 1.0, "tbsp"),
            ("sriracha (15g)", "1", 1.0, "tbsp"),
            ("honey", "1 1/2", 1.5, "tbsp"),
            ("Black pepper, to taste", None, None, None),
            ("non-fat Greek yogurt", "120", 120.0, "g"),
            ("sriracha", "1", 1.0, "tbsp"),
            ("honey (7g)", "1", 1.0, "tsp"),
            ("garlic powder", "1/2", 0.5, "tsp"),
            ("grated ginger", "1/2", 0.5, "tsp"),
            ("Water, to thin as needed", None, None, None),
        ],
        "steps": [
            "Sauté Aromatics: Heat a large skillet or wok over medium- high heat. Add sesame oil, onion, garlic, and ginger. Sauté 60–90 seconds until fragrant.",
            "Cook the Turkey: Add ground turkey and cook until browned and slightly crispy, breaking it up as it cooks.",
            "Add Veggies: Stir in coleslaw mix and cook 3–4 minutes until tender but still slightly crisp.",
            "Finish with Sauce: Pour in soy sauce, rice vinegar, hoisin, sriracha, honey, and black pepper. Stir well and simmer 2–3 minutes until everything is coated and flavorful.",
            "Make the Drizzle (Optional): Whisk Greek yogurt, sriracha, honey, rice vinegar, garlic powder, ginger, and water until smooth and drizzle-able.",
            "Serve: Enjoy hot topped with green onions and a drizzle of creamy ginger-garlic sauce, or portion into containers for easy meal prep.",
        ],
    },
    {
        "title": "Chicken Fried Rice",
        "page": 92,
        "serving": 4,
        "minutes": 40,
        "ingredients": [
            ("chicken breast", "18", 18.0, "oz"),
            ("brown rice and quinoa (2 pouches)", "2", 2.0, "cups"),
            ("bag garden duo (frozen peas and carrots)", "15", 15.0, "oz"),
            ("eggs", "4", 4.0, "large"),
            ("soy sauce", "2", 2.0, "tablespoons"),
            ("garlic cloves, minced", "3", 3.0, None),
            ("sesame oil", "1", 1.0, "tablespoon"),
            ("Salt and pepper, to taste", None, None, None),
            ("low-sodium soy sauce", "1/4", 0.25, "cup"),
            ("honey or maple syrup", "2", 2.0, "tablespoons"),
            ("rice vinegar", "1", 1.0, "tablespoon"),
            ("fresh ginger, grated", "1", 1.0, "tablespoon"),
            ("garlic, minced", "2", 2.0, "cloves"),
            ("sesame oil chili flakes for heat", "1", 1.0, "teaspoon"),
        ],
        "steps": [
            "Combine the marinade ingredients in a bowl. Add the chicken breast, ensuring it is evenly coated. Cover and refrigerate for at least 30 minutes. If short on time, reserve the marinade to use as a sauce instead.",
            "Heat a large skillet or wok over medium-high heat. Add the sesame oil. Remove the chicken from the marinade and cook for 6-8 minutes, flipping occasionally, until golden brown and cooked through. Remove from the skillet and set aside.",
            "In the same skillet, add the frozen peas and carrots. Sauté for 3-4 minutes until softened.",
            "Push the vegetables to the side and crack the eggs into the skillet. Scramble the eggs until fully cooked, then mix them with the vegetables.",
            "Add the brown rice and quinoa to the skillet along with the soy sauce and minced garlic. Stir well to combine.",
            "Slice the cooked chicken into bite-sized pieces and return it to the skillet. Stir everything together and cook for 2-3 minutes until heated through.",
            "Divide the fried rice evenly into 4 meal prep containers.",
        ],
    },
]


def _get_tag(db, name: str) -> models.Tag:
    """Case-insensitive get-or-create, matching recipes_service._get_or_create_tag."""
    tag = db.query(models.Tag).filter(func.lower(models.Tag.name) == name.lower()).first()
    if not tag:
        tag = models.Tag(name=name)
        db.add(tag)
        db.flush()
    return tag


def _ensure_user(db, user_id: str) -> None:
    """recipes.user_id is a FK to its own users table, which jarvis-auth does not fill."""
    if db.get(models.User, user_id) is None:
        db.add(models.User(user_id=user_id))
        db.flush()


def seed(db, user_id: str, household_id: str | None, dry_run: bool) -> tuple[int, int]:
    """Returns (created, skipped)."""
    created = skipped = 0

    if not dry_run:
        _ensure_user(db, user_id)

    for recipe_data in RECIPES:
        title = recipe_data["title"]
        if (
            db.query(models.Recipe)
            .filter(models.Recipe.user_id == user_id, models.Recipe.title == title)
            .first()
        ):
            print(f"  skip    {title}  (already present)")
            skipped += 1
            continue

        print(
            f"  create  {title}  [{len(recipe_data['ingredients'])} ingredients, "
            f"{len(recipe_data['steps'])} steps, serves {recipe_data['serving']}]"
        )
        created += 1
        if dry_run:
            continue

        recipe = models.Recipe(
            user_id=user_id,
            household_id=household_id,
            title=title,
            description=(
                f"From {SOURCE}, p.{recipe_data['page']} · "
                f"about {recipe_data['minutes']} minutes · serves {recipe_data['serving']}"
            ),
            source_type=models.SourceType.MANUAL,
            servings=recipe_data["serving"],
            total_time_minutes=recipe_data["minutes"],
        )
        # Add before touching .tags: _get_tag flushes, and appending to a
        # relationship on an object the session has not seen warns and silently
        # drops the association.
        db.add(recipe)

        recipe.tags.append(_get_tag(db, PICK_TAG))
        recipe.tags.append(_get_tag(db, "dinner"))

        for text, display, value, unit in recipe_data["ingredients"]:
            recipe.ingredients.append(
                models.Ingredient(
                    text=text, quantity_display=display, quantity_value=value, unit=unit
                )
            )
        for n, step_text in enumerate(recipe_data["steps"], start=1):
            recipe.steps.append(models.Step(step_number=n, text=step_text))

    # No rollback on the dry-run path: it stages nothing, and rolling back a
    # session this function does not own discards the caller's work.
    if not dry_run:
        db.commit()
    return created, skipped


def purge(db, dry_run: bool, user_id: str) -> int:
    """Delete this script's recipes for one user, matched by title.

    Deliberately not keyed on the "Alex" tag: that is a real tag the household
    uses, so purging by it would take recipes nobody asked to lose. Title
    matching is less precise instead -- a recipe written later under one of
    these names would match -- so every row is printed before deletion.
    """
    titles = [r["title"] for r in RECIPES]
    recipes = (
        db.query(models.Recipe)
        .filter(models.Recipe.user_id == user_id, models.Recipe.title.in_(titles))
        .all()
    )
    if not recipes:
        print(f"  nothing to remove for user {user_id}")
        return 0

    print(f"  matching {len(recipes)} row(s) by title -- check the list:")
    for recipe in recipes:
        print(f"  delete  {recipe.title}  (id={recipe.id})")
        if not dry_run:
            db.delete(recipe)

    if not dry_run:
        db.commit()
    return len(recipes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed cookbook picks.")
    parser.add_argument("--user-id", help="Author of the recipes (recipes.user_id).")
    parser.add_argument(
        "--household-id",
        help="Household the recipes are visible to. Required unless --no-household.",
    )
    parser.add_argument(
        "--no-household",
        action="store_true",
        help="Seed with household_id NULL -- visible only to the author.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing.")
    parser.add_argument("--purge", action="store_true", help="Delete these titles for --user-id.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.purge:
            if not args.user_id:
                parser.error("--purge needs --user-id: titles are matched per author.")
            print(f"PURGE{' (dry run)' if args.dry_run else ''}")
            removed = purge(db, args.dry_run, str(args.user_id))
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
        print(f"  source       {SOURCE}")
        print(f"  user_id      {args.user_id}")
        print(f"  household_id {args.household_id or 'NULL (author-only)'}")
        print(f"  tags         {PICK_TAG}, dinner")
        print(f"  recipes      {len(RECIPES)}\n")

        created, skipped = seed(db, str(args.user_id), args.household_id, args.dry_run)
        verb = "would be created" if args.dry_run else "created"
        print(f"\n{created} {verb}, {skipped} already present.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
