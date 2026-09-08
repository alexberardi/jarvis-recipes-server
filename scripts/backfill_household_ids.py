"""Assign household_id to rows created before household scoping existed.

Migration e5f6a7b8c9d0 adds the column but leaves it NULL, because this service
cannot resolve a user's household -- that mapping lives in jarvis-auth -- and a
migration has no business making network calls. Until this runs, NULL rows stay
visible to their author only (services/scoping.py), so nothing is lost; they are
just not shared with the household yet.

    python scripts/backfill_household_ids.py --dry-run
    python scripts/backfill_household_ids.py

--household is REQUIRED, and that is a limitation of jarvis-auth rather than a
shortcut here: it exposes no user -> household lookup that a service can call.
/internal/users/batch takes app credentials but returns only usernames
({"users": {"1": "jordan"}}), and /superuser/households needs a superuser JWT,
which a backfill script has no way to obtain. Resolving households automatically
would mean either a new internal endpoint on jarvis-auth or reading its database
across a service boundary. For a single-household install, passing the id is
both correct and simpler.

Users left unmapped are LEFT NULL rather than guessed. A wrong household_id is a
cross-family data leak; a NULL is merely un-shared, and this script is safe to
re-run once the mapping is known.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_recipes.app.db import models  # noqa: E402
from jarvis_recipes.app.db.session import SessionLocal  # noqa: E402

TABLES = (models.Recipe, models.MealPlan, models.RecipeParseJob)


def resolve_households(user_ids: list[str], household_id: str | None) -> dict[str, str]:
    """Map user ids to households.

    Only the explicit --household path exists today; see the module docstring for
    why. Kept as a seam so a future jarvis-auth endpoint (or a superuser-token
    path) slots in here without touching the update logic below.
    """
    if not household_id:
        return {}
    return {uid: household_id for uid in user_ids}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    ap.add_argument("--household", help="fallback household_id for unresolved users")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        pending: dict[str, int] = {}
        for model in TABLES:
            for (uid,) in db.query(model.user_id).filter(model.household_id.is_(None)).distinct():
                pending[uid] = pending.get(uid, 0) + 1

        if not pending:
            print("nothing to backfill")
            return

        print(f"users with un-backfilled rows: {sorted(pending)}")
        mapping = resolve_households(sorted(pending), args.household)
        if not mapping:
            print("  no --household given and no lookup available; nothing to do")

        unresolved = sorted(set(pending) - set(mapping))
        if unresolved:
            print(f"  unresolved (left NULL, still visible to their author): {unresolved}")

        total = 0
        for model in TABLES:
            for uid, household_id in mapping.items():
                n = (
                    db.query(model)
                    .filter(model.user_id == uid, model.household_id.is_(None))
                    .update({model.household_id: household_id}, synchronize_session=False)
                )
                if n:
                    print(f"  {model.__tablename__}: {n} rows -> {household_id} (user {uid})")
                    total += n

        if args.dry_run:
            db.rollback()
            print(f"dry run: {total} rows would be updated")
        else:
            db.commit()
            print(f"updated {total} rows")
    finally:
        db.close()


if __name__ == "__main__":
    main()
