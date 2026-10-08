"""Re-own recipes data after the move from the legacy stack to jarvisd.

jarvisd starts with a clean account database: everyone signs up again, gets a
new user id and a new household id. Every row here still carries the LEGACY
ids, so after cutover nobody sees their recipes -- or worse, a new user who
happens to get an old id sees someone else's. This script maps each legacy user
to their new jarvisd account by email (case-insensitive) and rewrites every
user- and household-owned column, in one transaction.

    # report only (the default) -- reads everything, writes nothing
    python -m scripts.remap_users \\
        --legacy-auth-db postgresql://postgres:...@localhost:5432/jarvis_auth \\
        --jarvisd-url http://localhost:7701 --jarvisd-email admin@example.com

    # do it
    python -m scripts.remap_users ... --apply

Inputs:
  --legacy-auth-db   the legacy jarvis-auth Postgres (tables users, households,
                     household_memberships). After cutover that is the T-1 dump
                     restored somewhere, or the stopped jarvis-postgres started
                     again just for this.
  --jarvisd-url      jarvisd's auth listener. Read with a SUPERUSER token
                     (GET /superuser/users lists every account with its
                     households): --jarvisd-token / JARVISD_TOKEN, or
                     --jarvisd-email plus JARVISD_PASSWORD (prompted if unset).
  --recipes-db       this service's database; defaults to DATABASE_URL.

Rules:
  * Users match by email, case-insensitive. Unmatched users' rows are left
    exactly as they are and listed in the report -- re-run once they sign up.
  * A legacy household maps to a household of its highest-ranking matched
    member (admin, then power_user, then member). When that person has several
    jarvisd households it narrows to one with the same name, then to one every
    matched member shares; still ambiguous (or no match at all) is reported and
    those rows are left alone. --household LEGACY=NEW settles one by hand.
  * Refused, nothing written: two legacy users matching one jarvisd account; two
    legacy households mapping to one new household (both merges, which can
    collide on unique constraints and silently combine data); and an unmatched
    legacy user id that is ALSO a jarvisd user id -- leaving those rows alone
    would hand them to a stranger. --park-unmatched rewrites such ids to
    "legacy-<id>", owned by nobody, so the rest can go ahead.
  * Idempotent: every rewrite is logged in `legacy_id_remap`, and ids already
    rewritten are never treated as legacy ids again. A second run with the same
    inputs changes nothing; a run after more people sign up maps only them.

Swaps are expected (legacy 1 -> 4 and legacy 4 -> 1 on a fresh jarvisd), so
values move through a temporary placeholder: a single UPDATE would trip unique
constraints row by row in Postgres.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    case,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Connection, Engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_recipes.app.core.config import get_settings  # noqa: E402

# Every column holding a jarvis-auth user id or household id.
# tests/test_remap_users.py fails if a model gains one that is not listed here.
USER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recipes", "user_id"),
    ("meal_plans", "user_id"),
    ("grocery_sku_map", "user_id"),
    ("recipe_parse_jobs", "user_id"),
    ("recipe_ingestions", "user_id"),
    ("mailbox_messages", "user_id"),
    ("stage_recipes", "user_id"),
    ("staples", "user_id"),
    ("settings", "user_id"),  # INTEGER, unlike the rest
)
HOUSEHOLD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("recipes", "household_id"),
    ("meal_plans", "household_id"),
    ("grocery_sku_map", "household_id"),
    ("recipe_parse_jobs", "household_id"),
    ("staples", "household_id"),
    ("settings", "household_id"),
)
# The local mirror of auth's users; every user_id FK points here.
USERS_TABLE = "users"
INTEGER_USER_COLUMNS = {("settings", "user_id")}

ROLE_RANK = {"admin": 0, "power_user": 1, "member": 2}
TEMP_PREFIX = "__remap__"
PARK_PREFIX = "legacy-"

remap_log_metadata = MetaData()
remap_log = Table(
    "legacy_id_remap",
    remap_log_metadata,
    Column("id", Integer, primary_key=True),
    Column("kind", String(16), nullable=False),  # "user" | "household"
    Column("old_id", String(255), nullable=False),
    Column("new_id", String(255), nullable=False),
    Column("applied_at", DateTime, nullable=False),
)


# --- directories -------------------------------------------------------------


@dataclass
class LegacyDirectory:
    """The legacy jarvis-auth accounts."""

    emails: dict[str, str]  # user id -> email
    household_names: dict[str, str]  # household id -> name
    memberships: list[tuple[str, str, str]]  # (household id, user id, role), in membership order


@dataclass
class JarvisdDirectory:
    """jarvisd's accounts, from GET /superuser/users."""

    emails: dict[str, str]  # user id -> email
    households: dict[str, list[tuple[str, str]]]  # user id -> [(household id, name)]


def load_legacy(dsn: str) -> LegacyDirectory:
    engine = create_engine(dsn, future=True)
    try:
        with engine.connect() as conn:
            emails = {str(r[0]): r[1] for r in conn.execute(text("SELECT id, email FROM users"))}
            names = {str(r[0]): r[1] for r in conn.execute(text("SELECT id, name FROM households"))}
            members = [
                (str(r[0]), str(r[1]), str(r[2]).lower())
                for r in conn.execute(
                    text("SELECT household_id, user_id, role FROM household_memberships ORDER BY id")
                )
            ]
    finally:
        engine.dispose()
    return LegacyDirectory(emails=emails, household_names=names, memberships=members)


def jarvisd_token(base_url: str, token: str | None, email: str | None, password: str | None) -> str:
    if token:
        return token
    if not email:
        raise SystemExit("jarvisd needs a superuser: pass --jarvisd-token (or JARVISD_TOKEN) or --jarvisd-email")
    if password is None:
        password = getpass.getpass(f"jarvisd password for {email}: ")
    resp = httpx.post(f"{base_url.rstrip('/')}/auth/login", json={"email": email, "password": password}, timeout=10)
    if resp.status_code != 200:
        raise SystemExit(f"jarvisd login failed: HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()["access_token"]


def load_jarvisd(base_url: str, token: str) -> JarvisdDirectory:
    resp = httpx.get(
        f"{base_url.rstrip('/')}/superuser/users", headers={"Authorization": f"Bearer {token}"}, timeout=10
    )
    if resp.status_code != 200:
        raise SystemExit(f"jarvisd /superuser/users: HTTP {resp.status_code}: {resp.text[:200]} (superuser token?)")
    return jarvisd_directory_from(resp.json())


def jarvisd_directory_from(users: list[dict[str, Any]]) -> JarvisdDirectory:
    return JarvisdDirectory(
        emails={str(u["id"]): u["email"] for u in users},
        households={
            str(u["id"]): [(h["household_id"], h.get("household_name") or "") for h in u.get("households") or []]
            for u in users
        },
    )


# --- the plan ----------------------------------------------------------------


@dataclass
class Plan:
    user_map: dict[str, str] = field(default_factory=dict)  # legacy id -> new id (or a parked id)
    household_map: dict[str, str] = field(default_factory=dict)
    matched_users: list[tuple[str, str, str]] = field(default_factory=list)  # (old, new, email)
    unmatched_users: list[tuple[str, str]] = field(default_factory=list)  # (old, reason)
    parked_users: list[str] = field(default_factory=list)
    unmatched_households: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)  # "table.column" -> rows to rewrite
    already_applied: int = 0

    @property
    def changes(self) -> int:
        return sum(self.counts.values())


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def build_plan(
    legacy: LegacyDirectory,
    jarvisd: JarvisdDirectory,
    user_ids: Iterable[str],
    household_ids: Iterable[str],
    applied_users: set[str] = frozenset(),  # type: ignore[assignment]
    applied_households: set[str] = frozenset(),  # type: ignore[assignment]
    overrides: dict[str, str] | None = None,
    park_unmatched: bool = False,
) -> Plan:
    """Decide every rewrite without touching a database.

    `user_ids` / `household_ids` are the distinct values found in the recipes
    DB; the `applied_*` sets are values an earlier run already wrote, which are
    new ids and must never be read as legacy ones.
    """
    plan = Plan()
    new_by_email: dict[str, str] = {}
    for uid, email in jarvisd.emails.items():
        new_by_email[_norm(email)] = uid

    claimed: dict[str, str] = {}
    for old in sorted(set(user_ids) - set(applied_users), key=_id_sort):
        if old.startswith(PARK_PREFIX):
            continue  # parked by an earlier run; owned by nobody on purpose
        email = legacy.emails.get(old)
        if email is None:
            plan.unmatched_users.append((old, "no legacy account with this id"))
            continue
        new = new_by_email.get(_norm(email))
        if new is None:
            plan.unmatched_users.append((old, f"{email}: no jarvisd account with this email"))
            continue
        if new in claimed:
            plan.errors.append(
                f"legacy users {claimed[new]} and {old} both match jarvisd user {new} ({email}); refusing to merge them"
            )
            continue
        claimed[new] = old
        plan.matched_users.append((old, new, email))
        if old != new:
            plan.user_map[old] = new

    taken_by_earlier_run = set(applied_users)
    for old, new, email in plan.matched_users:
        if new in taken_by_earlier_run:
            plan.errors.append(
                f"jarvisd user {new} ({email}) already received another legacy user's rows in an earlier run"
            )

    # An unmatched legacy id that is also a jarvisd id would hand its rows to that
    # jarvisd user. Refuse unless parking it out of the way was asked for.
    targets = set(jarvisd.emails)
    for old, reason in list(plan.unmatched_users):
        if old in targets:
            if park_unmatched:
                plan.user_map[old] = PARK_PREFIX + old
                plan.parked_users.append(old)
            else:
                plan.errors.append(
                    f"unmatched legacy user {old} ({reason}) has the same id as jarvisd user {old} "
                    f"({jarvisd.emails[old]}); leaving those rows would give them to that account. "
                    f"Have them sign up, or pass --park-unmatched"
                )

    _plan_households(plan, legacy, jarvisd, household_ids, applied_households, overrides or {})
    return plan


def _id_sort(value: str) -> tuple[int, Any]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _plan_households(
    plan: Plan,
    legacy: LegacyDirectory,
    jarvisd: JarvisdDirectory,
    household_ids: Iterable[str],
    applied: set[str],
    overrides: dict[str, str],
) -> None:
    new_of = {old: new for old, new, _ in plan.matched_users}
    all_new_households = {h for hs in jarvisd.households.values() for h, _ in hs}
    members_of: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for household, user, role in legacy.memberships:
        members_of[household].append((user, role))

    for old in sorted(set(household_ids) - set(applied)):
        if old in overrides:
            if overrides[old] not in all_new_households:
                plan.errors.append(f"--household {old}={overrides[old]}: no such jarvisd household")
            elif overrides[old] != old:
                plan.household_map[old] = overrides[old]
            continue
        if old in all_new_households:
            continue  # already a jarvisd household
        if old not in legacy.household_names:
            plan.unmatched_households.append((old, "no legacy household with this id"))
            continue
        ranked = sorted(
            ((user, role) for user, role in members_of[old] if user in new_of),
            key=lambda ur: ROLE_RANK.get(ur[1], 9),
        )
        if not ranked:
            plan.unmatched_households.append((old, "no member of it has a jarvisd account yet"))
            continue
        owner_new = new_of[ranked[0][0]]
        candidates = jarvisd.households.get(owner_new, [])
        if len(candidates) > 1:
            name = legacy.household_names[old].strip().lower()
            same_name = [c for c in candidates if c[1].strip().lower() == name]
            if len(same_name) == 1:
                candidates = same_name
            else:
                shared = set.intersection(
                    *({h for h, _ in jarvisd.households.get(new_of[u], [])} for u, _ in ranked)
                )
                narrowed = [c for c in candidates if c[0] in shared]
                candidates = narrowed if len(narrowed) == 1 else candidates
        if not candidates:
            plan.unmatched_households.append(
                (old, f"its owner (jarvisd user {owner_new}) belongs to no jarvisd household")
            )
        elif len(candidates) > 1:
            names = ", ".join(f"{h} ({n})" for h, n in candidates)
            plan.unmatched_households.append(
                (old, f"ambiguous: jarvisd user {owner_new} is in {names}; settle it with --household {old}=<id>")
            )
        else:
            plan.household_map[old] = candidates[0][0]

    seen: dict[str, str] = {}
    for old, new in plan.household_map.items():
        if new in seen:
            plan.errors.append(
                f"legacy households {seen[new]} and {old} both map to jarvisd household {new}; refusing to merge them"
            )
        seen[new] = old


# --- reading and writing the recipes DB --------------------------------------


def _tables(conn: Connection) -> dict[str, Table]:
    md = MetaData()
    md.reflect(bind=conn, only=sorted({t for t, _ in USER_COLUMNS + HOUSEHOLD_COLUMNS} | {USERS_TABLE}))
    return dict(md.tables)


def distinct_ids(conn: Connection) -> tuple[set[str], set[str]]:
    tables = _tables(conn)
    users: set[str] = {str(v) for (v,) in conn.execute(select(tables[USERS_TABLE].c.user_id))}
    households: set[str] = set()
    for name, col in USER_COLUMNS:
        users |= {str(v) for (v,) in conn.execute(select(tables[name].c[col]).distinct()) if v is not None}
    for name, col in HOUSEHOLD_COLUMNS:
        households |= {str(v) for (v,) in conn.execute(select(tables[name].c[col]).distinct()) if v is not None}
    return users, households


def applied_ids(conn: Connection) -> tuple[set[str], set[str], int]:
    remap_log_metadata.create_all(conn, checkfirst=True)
    rows = conn.execute(select(remap_log.c.kind, remap_log.c.new_id)).all()
    return (
        {new for kind, new in rows if kind == "user"},
        {new for kind, new in rows if kind == "household"},
        len(rows),
    )


def count_changes(conn: Connection, plan: Plan) -> dict[str, int]:
    tables = _tables(conn)
    counts: dict[str, int] = {}
    for columns, mapping in ((USER_COLUMNS + ((USERS_TABLE, "user_id"),), plan.user_map), (HOUSEHOLD_COLUMNS, plan.household_map)):
        if not mapping:
            continue
        for name, col in columns:
            column = tables[name].c[col]
            olds = _typed(name, col, mapping)
            n = conn.execute(select(func.count()).select_from(tables[name]).where(column.in_(list(olds)))).scalar_one()
            if n:
                counts[f"{name}.{col}"] = n
    return counts


def _typed(table: str, col: str, mapping: dict[str, str]) -> dict[Any, Any]:
    if (table, col) in INTEGER_USER_COLUMNS:
        # Parked ids are not integers; an INTEGER column cannot hold one.
        return {int(o): int(n) for o, n in mapping.items() if o.isdigit() and n.isdigit()}
    return dict(mapping)


def _rewrite(conn: Connection, table: Table, col: str, mapping: dict[Any, Any], temp_of) -> None:
    """Move every old value to its new one through a placeholder, so a swap never
    has two rows holding the same value mid-statement."""
    column = table.c[col]
    temps = {old: temp_of(old) for old in mapping}
    conn.execute(update(table).where(column.in_(list(mapping))).values({col: case(temps, value=column)}))
    finals = {temps[old]: new for old, new in mapping.items()}
    conn.execute(update(table).where(column.in_(list(finals))).values({col: case(finals, value=column)}))


def apply_plan(conn: Connection, plan: Plan) -> None:
    """Every rewrite, inside the caller's transaction."""
    tables = _tables(conn)
    users = tables[USERS_TABLE]
    now = datetime.utcnow()

    if plan.user_map:
        existing = {str(v) for (v,) in conn.execute(select(users.c.user_id))}
        targets = set(plan.user_map.values())
        temps = {TEMP_PREFIX + old for old in plan.user_map}
        # FK targets for the placeholder and the final values must exist while
        # the children move; the placeholders go again at the end.
        to_create = sorted((targets | temps) - existing)
        if to_create:
            conn.execute(insert(users), [{"user_id": v} for v in to_create])
        for name, col in USER_COLUMNS:
            mapping = _typed(name, col, plan.user_map)
            if not mapping:
                continue
            if (name, col) in INTEGER_USER_COLUMNS:
                _rewrite(conn, tables[name], col, mapping, lambda old: -(old + 1))
            else:
                _rewrite(conn, tables[name], col, mapping, lambda old: TEMP_PREFIX + old)
        # Old mirror rows nobody points at any more (a target that is also an old
        # id -- a swap -- keeps its row, now owning the other user's data).
        stale = (set(plan.user_map) - targets) | temps
        conn.execute(delete(users).where(users.c.user_id.in_(sorted(stale))))

    for name, col in HOUSEHOLD_COLUMNS:
        if plan.household_map:
            _rewrite(conn, tables[name], col, dict(plan.household_map), lambda old: TEMP_PREFIX + old)

    log_rows = [
        {"kind": "user", "old_id": old, "new_id": new, "applied_at": now} for old, new in plan.user_map.items()
    ] + [{"kind": "household", "old_id": old, "new_id": new, "applied_at": now} for old, new in plan.household_map.items()]
    # Matched users whose id did not change are logged too, so a later run never
    # mistakes them for a legacy id.
    log_rows += [
        {"kind": "user", "old_id": old, "new_id": new, "applied_at": now}
        for old, new, _ in plan.matched_users
        if old == new
    ]
    if log_rows:
        conn.execute(insert(remap_log), log_rows)


def plan_for(
    engine: Engine,
    legacy: LegacyDirectory,
    jarvisd: JarvisdDirectory,
    overrides: dict[str, str] | None = None,
    park_unmatched: bool = False,
) -> Plan:
    with engine.begin() as conn:
        applied_users, applied_households, n_applied = applied_ids(conn)
        user_ids, household_ids = distinct_ids(conn)
        plan = build_plan(
            legacy, jarvisd, user_ids, household_ids, applied_users, applied_households, overrides, park_unmatched
        )
        plan.already_applied = n_applied
        plan.counts = count_changes(conn, plan)
    return plan


def run(
    engine: Engine,
    legacy: LegacyDirectory,
    jarvisd: JarvisdDirectory,
    apply: bool,
    overrides: dict[str, str] | None = None,
    park_unmatched: bool = False,
) -> Plan:
    plan = plan_for(engine, legacy, jarvisd, overrides, park_unmatched)
    if apply and not plan.errors and (plan.user_map or plan.household_map or plan.matched_users):
        with engine.begin() as conn:
            apply_plan(conn, plan)
    return plan


def report(plan: Plan, applied: bool) -> str:
    lines = [f"recipes user remap ({'APPLIED' if applied else 'dry run: nothing written'})", ""]
    if plan.already_applied:
        lines.append(f"{plan.already_applied} id(s) already rewritten by an earlier run (left as they are)")
    lines.append(f"Matched users ({len(plan.matched_users)}):")
    for old, new, email in plan.matched_users:
        lines.append(f"  legacy {old} -> jarvisd {new}  {email}" + ("  (same id)" if old == new else ""))
    lines.append(f"Unmatched users ({len(plan.unmatched_users)}), rows left untouched:")
    for old, reason in plan.unmatched_users:
        parked = "  -> parked as " + PARK_PREFIX + old if old in plan.parked_users else ""
        lines.append(f"  legacy {old}: {reason}{parked}")
    lines.append(f"Households mapped ({len(plan.household_map)}):")
    for old, new in plan.household_map.items():
        lines.append(f"  {old} -> {new}")
    lines.append(f"Households not mapped ({len(plan.unmatched_households)}), rows left untouched:")
    for old, reason in plan.unmatched_households:
        lines.append(f"  {old}: {reason}")
    lines.append(f"Rows to rewrite ({plan.changes}):" if not applied else f"Rows rewritten ({plan.changes}):")
    for key, n in sorted(plan.counts.items()):
        lines.append(f"  {key}: {n}")
    if plan.errors:
        lines.append("")
        lines.append("REFUSED -- nothing was written:")
        lines += [f"  {e}" for e in plan.errors]
    return "\n".join(lines)


def _overrides(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in values:
        old, sep, new = v.partition("=")
        if not sep or not old or not new:
            raise SystemExit(f"--household takes LEGACY_ID=NEW_ID, got {v!r}")
        out[old.strip()] = new.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--legacy-auth-db", required=True, help="legacy jarvis-auth database URL")
    ap.add_argument("--jarvisd-url", required=True, help="jarvisd auth base URL, e.g. http://host:7701")
    ap.add_argument("--jarvisd-token", default=os.getenv("JARVISD_TOKEN"), help="superuser access token")
    ap.add_argument("--jarvisd-email", help="superuser email to log in with (password: JARVISD_PASSWORD or prompt)")
    ap.add_argument("--recipes-db", default=None, help="recipes database URL (default: DATABASE_URL)")
    ap.add_argument("--household", action="append", default=[], metavar="LEGACY=NEW", help="map a household by hand")
    ap.add_argument("--park-unmatched", action="store_true", help="move colliding unmatched ids to legacy-<id>")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: report only)")
    args = ap.parse_args(argv)

    recipes_db = args.recipes_db or get_settings().database_url

    legacy = load_legacy(args.legacy_auth_db)
    token = jarvisd_token(args.jarvisd_url, args.jarvisd_token, args.jarvisd_email, os.getenv("JARVISD_PASSWORD"))
    jarvisd = load_jarvisd(args.jarvisd_url, token)

    engine = create_engine(recipes_db, future=True)
    try:
        plan = run(engine, legacy, jarvisd, args.apply, _overrides(args.household), args.park_unmatched)
    finally:
        engine.dispose()
    print(report(plan, applied=args.apply and not plan.errors))
    return 1 if plan.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
