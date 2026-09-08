"""Turning a shopping list into a retailer cart.

Three tiers, cheapest first, and the order matters:

1. **Exact** -- the normalized ingredient name is already in the household's map.
   A dictionary lookup. This is the steady state: once a household has shopped a
   few times, almost everything hits here.
2. **LLM** -- an ingredient the map has never seen, matched against the entries it
   HAS seen. "90/10 ground beef" resolving to the household's saved
   "ground beef, 1 lb" is a judgement call, not a string comparison. This runs in
   the background and writes the alias, so tier 1 answers next time.
3. **The person** -- whatever is left comes back as `unmatched` for the picker.

The LLM never invents a SKU. It only ever chooses among entries the household
already saved, or declines. A hallucinated Walmart item id is a wrong thing in
someone's cart, discovered at the checkout, and there is no way for the model to
know it got it wrong.

Cart building is deliberately NOT blocked on the LLM. The export returns
immediately with everything tier 1 matched and enqueues tier 2 to improve the map
for next time -- a shopper standing in the kitchen should not wait on a model to
get a link.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.auth import CurrentUser
from jarvis_recipes.app.services import shopping_list_service
from jarvis_recipes.app.services.scoping import household_for_write, visible_to

logger = logging.getLogger(__name__)

# affil.walmart.com is Walmart's own affiliate deep-link host and the only
# documented way to preload a cart from outside the app. Items are
# `<itemId>_<qty>` pairs, comma separated.
WALMART_CART_URL = "https://affil.walmart.com/cart/addToCart?items={items}"

RETAILERS = ("walmart",)


@dataclass
class CartItem:
    ingredient_name: str
    sku: str
    quantity: int
    product_name: Optional[str] = None
    unit_size: Optional[str] = None
    source: str = "manual"


@dataclass
class UnmatchedItem:
    ingredient_name: str
    # Carried through so the picker can search for something meaningful rather
    # than the bare noun -- "2 lb beef sirloin" finds a product, "beef" does not.
    amount_display: str
    recipes: list[str]


@dataclass
class Cart:
    retailer: str
    url: Optional[str]
    items: list[CartItem]
    unmatched: list[UnmatchedItem]
    # Set when a background matching pass was enqueued for the unmatched names.
    match_job_id: Optional[str] = None


# ── the map ───────────────────────────────────────────────────────────────────


def list_map(db: Session, user: CurrentUser, retailer: str = "walmart") -> list[models.GrocerySkuMap]:
    stmt = (
        select(models.GrocerySkuMap)
        .where(visible_to(models.GrocerySkuMap, user))
        .where(models.GrocerySkuMap.retailer == retailer)
        .order_by(models.GrocerySkuMap.ingredient_name)
    )
    return list(db.scalars(stmt))


def map_key(text: str) -> str:
    """The map's key for a RAW ingredient line.

    Apply this at the boundary where a recipe line enters, and nowhere else --
    `normalize_name` is NOT idempotent. It strips a leading quantity, so running
    it on an already-normalized name eats the front of anything that legitimately
    begins with digits: "90/10 ground beef" becomes "ground beef", "2% milk"
    becomes "milk", "7up" becomes "up". Every one of those is a different grocery
    item from what it collapses to, and the alias silently points at the wrong
    product.
    """
    return shopping_list_service.normalize_name(text)


def upsert_mapping(
    db: Session,
    user: CurrentUser,
    ingredient_name: str,
    sku: str,
    retailer: str = "walmart",
    product_name: Optional[str] = None,
    unit_size: Optional[str] = None,
    source: str = "manual",
) -> models.GrocerySkuMap:
    """Save what this household buys for an ingredient.

    `ingredient_name` is a shopping-list key -- what `shopping_list_service.build`
    produced -- not a raw recipe line. Callers holding a raw line pass it through
    `map_key` first. Stored case-folded and stripped, which is idempotent; see
    `map_key` for why re-normalizing here would not be.

    An LLM guess never overwrites a manual choice. The background pass runs on a
    stale view of the map and would otherwise undo a correction made seconds
    earlier, with nothing on screen to explain it.
    """
    name = (ingredient_name or "").strip().lower()
    existing = _find_mapping(db, user, name, retailer)

    if existing is not None:
        if source == "llm" and existing.source == "manual":
            return existing
        existing.sku = sku
        existing.product_name = product_name
        existing.unit_size = unit_size
        existing.source = source
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing

    mapping = models.GrocerySkuMap(
        user_id=str(user.id),
        household_id=household_for_write(user),
        retailer=retailer,
        ingredient_name=name,
        sku=sku,
        product_name=product_name,
        unit_size=unit_size,
        source=source,
    )
    db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


def delete_mapping(db: Session, user: CurrentUser, mapping_id: int) -> bool:
    stmt = (
        select(models.GrocerySkuMap)
        .where(models.GrocerySkuMap.id == mapping_id)
        .where(visible_to(models.GrocerySkuMap, user))
    )
    mapping = db.scalars(stmt).first()
    if mapping is None:
        return False
    db.delete(mapping)
    db.commit()
    return True


def _find_mapping(
    db: Session, user: CurrentUser, normalized_name: str, retailer: str
) -> Optional[models.GrocerySkuMap]:
    stmt = (
        select(models.GrocerySkuMap)
        .where(visible_to(models.GrocerySkuMap, user))
        .where(models.GrocerySkuMap.retailer == retailer)
        .where(models.GrocerySkuMap.ingredient_name == normalized_name)
    )
    return db.scalars(stmt).first()


# ── the cart ──────────────────────────────────────────────────────────────────


_PACK_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?")


def _parse_pack_size(unit_size: str) -> tuple[Optional[Decimal], Optional[str]]:
    """Split a stored package size like "1 lb" or "12 oz" into number and unit.

    Local rather than in shopping_list_service: that module parses recipe LINES,
    where the number is a quantity of an ingredient. This parses a product's
    package size. They look alike and mean different things, and merging them
    would invite using one where the other belongs.
    """
    match = _PACK_SIZE.match(unit_size or "")
    if not match:
        return None, None
    try:
        return Decimal(match.group(1)), (match.group(2) or None)
    except InvalidOperation:
        return None, None


def _pack_quantity(item: shopping_list_service.ShoppingItem, unit_size: Optional[str]) -> int:
    """How many of the mapped product to buy.

    Deliberately crude, and biased upward. Real unit conversion between "1.4 lb"
    and a "1 lb package" needs a density table and still gets vegetables wrong;
    what a shopper actually needs is not to come home short. So: if we know the
    package size and the units agree, round UP to cover the recipe; otherwise buy
    one and let them adjust in the cart.

    Never returns 0 -- an ingredient on the list is an ingredient to buy.
    """
    if not unit_size:
        return 1

    size, size_unit = _parse_pack_size(unit_size)
    if size is None or size <= 0:
        return 1

    for amount in item.amounts:
        if amount.quantity is None:
            continue
        # Only when the units match. "3 cloves" against a "2 lb" bag is not a
        # ratio, and pretending it is puts 0 or 40 in the cart.
        if (amount.unit or "").lower() != (size_unit or "").lower():
            continue
        return max(1, math.ceil(float(amount.quantity) / float(size)))

    return 1


def _amount_display(item: shopping_list_service.ShoppingItem) -> str:
    """The amounts as a shopper would read them, for the picker's search box."""
    parts: list[str] = []
    for amount in item.amounts:
        if amount.quantity is not None:
            qty = f"{amount.quantity.normalize():f}".rstrip("0").rstrip(".")
            parts.append(f"{qty} {amount.unit}".strip() if amount.unit else qty)
        parts.extend(amount.unparsed)
    return ", ".join(p for p in parts if p)


def build_cart(
    db: Session,
    user: CurrentUser,
    start_date: date,
    end_date: date,
    retailer: str = "walmart",
) -> Cart:
    """Everything the map knows how to buy, as a cart link, plus what it doesn't."""
    if retailer not in RETAILERS:
        raise ValueError(f"Unsupported retailer: {retailer}")

    items = shopping_list_service.build(db, user, start_date, end_date)
    mappings = {m.ingredient_name: m for m in list_map(db, user, retailer)}

    matched: list[CartItem] = []
    unmatched: list[UnmatchedItem] = []

    for item in items:
        mapping = mappings.get(item.name)
        if mapping is None:
            unmatched.append(
                UnmatchedItem(
                    ingredient_name=item.name,
                    amount_display=_amount_display(item),
                    recipes=item.recipes,
                )
            )
            continue
        matched.append(
            CartItem(
                ingredient_name=item.name,
                sku=mapping.sku,
                quantity=_pack_quantity(item, mapping.unit_size),
                product_name=mapping.product_name,
                unit_size=mapping.unit_size,
                source=mapping.source,
            )
        )

    return Cart(
        retailer=retailer,
        url=cart_url(matched, retailer),
        items=matched,
        unmatched=unmatched,
    )


def cart_url(items: Iterable[CartItem], retailer: str = "walmart") -> Optional[str]:
    """The deep link, or None when there is nothing to put in it.

    An empty `items=` parameter opens Walmart with an empty cart and no
    explanation, which reads as a broken export rather than an empty list.
    """
    pairs = [f"{i.sku}_{i.quantity}" for i in items if i.sku]
    if not pairs:
        return None
    return WALMART_CART_URL.format(items=quote(",".join(pairs), safe=","))


# ── the background matching pass ──────────────────────────────────────────────

# Deliberately small. The prompt carries every candidate, so a household with a
# large map would otherwise blow the context window on one call; and a person
# looking at a long unmatched list wants the picker, not a slow model.
MAX_CANDIDATES = 60
MAX_UNMATCHED_PER_PASS = 25

_MATCH_SYSTEM_PROMPT = (
    "You map recipe ingredients onto grocery products a household has already "
    "bought.\n\n"
    "You are given CANDIDATES (products already in their list, each with an id) "
    "and INGREDIENTS that have no product yet.\n\n"
    "For each ingredient, choose the candidate id that is the SAME GROCERY ITEM, "
    "or null if none is.\n\n"
    "Rules:\n"
    "- Different amounts of the same thing DO match: '1.4 lb 90/10 ground beef' "
    "is the candidate 'ground beef'.\n"
    "- Different forms of the same thing DO match: 'garlic cloves' is 'garlic'.\n"
    "- Related but distinct items DO NOT match: 'sour cream' is not 'cream', "
    "'chicken thighs' is not 'chicken broth', 'buttermilk' is not 'butter'.\n"
    "- When unsure, answer null. A wrong product ends up in someone's cart and is "
    "only discovered at the checkout.\n\n"
    'Return ONLY JSON: {"matches": [{"ingredient": "<exact input string>", '
    '"candidate_id": <id or null>}]}'
)


def build_match_prompt(candidates: list[models.GrocerySkuMap], unmatched: list[str]) -> list[dict]:
    """The two messages sent to the proxy. Split out so a test can read them."""
    listing = "\n".join(
        f"{c.id}: {c.ingredient_name}"
        + (f" ({c.product_name})" if c.product_name else "")
        for c in candidates[:MAX_CANDIDATES]
    )
    wanted = "\n".join(f"- {name}" for name in unmatched[:MAX_UNMATCHED_PER_PASS])
    return [
        {"role": "system", "content": _MATCH_SYSTEM_PROMPT},
        {"role": "user", "content": f"CANDIDATES:\n{listing}\n\nINGREDIENTS:\n{wanted}"},
    ]


def apply_matches(
    db: Session,
    user: CurrentUser,
    matches: list[dict],
    retailer: str = "walmart",
) -> list[models.GrocerySkuMap]:
    """Write the model's choices into the map as aliases.

    Every id is checked against what this household can actually see before it is
    used. The model is asked to return ids from a list we gave it, but a model
    returning something else is a normal Tuesday, and an unchecked id here would
    be an IDOR: another household's SKU written into this one's map.
    """
    visible = {c.id: c for c in list_map(db, user, retailer)}
    written: list[models.GrocerySkuMap] = []

    for match in matches:
        name = (match or {}).get("ingredient")
        candidate_id = (match or {}).get("candidate_id")
        if not name or candidate_id is None:
            continue
        candidate = visible.get(candidate_id)
        if candidate is None:
            logger.warning(
                "Grocery match named candidate %r which is not visible to user %s; ignoring",
                candidate_id,
                user.id,
            )
            continue
        written.append(
            upsert_mapping(
                db,
                user,
                ingredient_name=name,
                sku=candidate.sku,
                retailer=retailer,
                product_name=candidate.product_name,
                unit_size=candidate.unit_size,
                source="llm",
            )
        )
    return written


def enqueue_match_pass(
    db: Session,
    user: CurrentUser,
    unmatched: list[str],
    retailer: str = "walmart",
) -> Optional[str]:
    """Queue a background pass to learn aliases for `unmatched`. Returns the job id.

    Skipped, returning None, when there is nothing to learn FROM: with an empty
    map the model has no candidates and can only invent, which is the one thing it
    must not do. The first few products have to come from the person.
    """
    if not unmatched:
        return None
    if not list_map(db, user, retailer):
        logger.info("No saved products yet for user %s; skipping match pass", user.id)
        return None

    # Imported here: parse_job_service imports queue_service, which opens a Redis
    # connection at module scope in some paths. Keeping it local means importing
    # grocery_service (for cart building, in the API process) does not.
    from jarvis_recipes.app.services import parse_job_service

    job = parse_job_service.create_job(
        db,
        user_id=str(user.id),
        job_type="grocery_match",
        household_id=user.household_id,
        job_data={
            "retailer": retailer,
            "unmatched": unmatched[:MAX_UNMATCHED_PER_PASS],
        },
    )
    return job.id
