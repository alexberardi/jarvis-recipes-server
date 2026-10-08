"""scripts/remap_users.py: legacy user/household ids -> jarvisd ids, by email.

The fixture mirrors prod's shape: legacy users 1 and 4 own everything, and on a
fresh jarvisd they sign up in the other order, so their ids SWAP (1 -> 2, 4 -> 1
here). A swap is the case a naive UPDATE gets wrong, so most tests run through
one, on SQLite with foreign keys enforced like Postgres.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine

from jarvis_recipes.app.db import models
from jarvis_recipes.app.db.base import Base

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "remap_users.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("_remap_users", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_remap_users"] = module
    spec.loader.exec_module(module)
    return module


remap = _load_script()

H_LEGACY = "11111111-1111-1111-1111-111111111111"  # the family, legacy
H_LEGACY_OTHER = "22222222-2222-2222-2222-222222222222"  # user 4's second, legacy
H_NEW = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
H_NEW_WORK = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _fk_engine(path: Path) -> Engine:
    engine = create_engine(f"sqlite+pysqlite:///{path}", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture
def legacy(tmp_path: Path) -> remap.LegacyDirectory:
    """A real legacy-shaped auth DB, read through load_legacy (enum names upper-case, as in Postgres)."""
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)"))
        conn.execute(text("CREATE TABLE households (id TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(
            text("CREATE TABLE household_memberships (id INTEGER PRIMARY KEY, household_id TEXT, user_id INTEGER, role TEXT)")
        )
        conn.execute(
            text("INSERT INTO users VALUES (1, 'Alex@Example.com'), (4, 'sam@example.com'), (5, 'gone@example.com')")
        )
        conn.execute(text(f"INSERT INTO households VALUES ('{H_LEGACY}', 'Home'), ('{H_LEGACY_OTHER}', 'Cabin')"))
        conn.execute(
            text(
                "INSERT INTO household_memberships VALUES "
                f"(1, '{H_LEGACY}', 1, 'ADMIN'), (2, '{H_LEGACY}', 4, 'MEMBER'), (3, '{H_LEGACY_OTHER}', 4, 'ADMIN')"
            )
        )
    engine.dispose()
    return remap.load_legacy(f"sqlite+pysqlite:///{tmp_path / 'auth.db'}")


def jarvisd_users() -> list[dict[str, Any]]:
    """GET /superuser/users as jarvisd returns it (internal/modules/auth/admin.go handleSuperUsers)."""
    users = [
        {"id": 1, "email": "sam@example.com", "username": "sam", "is_active": True, "is_superuser": False,
         "households": [{"household_id": H_NEW, "household_name": "Home", "role": "member"},
                        {"household_id": H_NEW_WORK, "household_name": "Cabin", "role": "admin"}]},
        {"id": 2, "email": "alex@example.com", "username": "alex", "is_active": True, "is_superuser": True,
         "households": [{"household_id": H_NEW, "household_name": "Home", "role": "admin"}]},
    ]
    return users


@pytest.fixture
def jarvisd() -> remap.JarvisdDirectory:
    return remap.jarvisd_directory_from(jarvisd_users())


@pytest.fixture
def recipes_db(tmp_path: Path) -> Engine:
    engine = _fk_engine(tmp_path / "recipes.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(models.User.__table__.insert(), [{"user_id": "1"}, {"user_id": "4"}, {"user_id": "9"}])
        conn.execute(
            models.Recipe.__table__.insert(),
            [
                {"id": 1, "user_id": "1", "household_id": H_LEGACY, "title": "Crepes", "source_type": "MANUAL"},
                {"id": 2, "user_id": "1", "household_id": None, "title": "Old one", "source_type": "MANUAL"},
                {"id": 3, "user_id": "9", "household_id": None, "title": "Orphan", "source_type": "MANUAL"},
            ],
        )
        conn.execute(
            models.MealPlan.__table__.insert(),
            [
                {"id": 1, "user_id": "1", "household_id": H_LEGACY, "start_date": date(2026, 9, 1)},
                {"id": 2, "user_id": "4", "household_id": H_LEGACY_OTHER, "start_date": date(2026, 9, 1)},
            ],
        )
        # A unique constraint per author: both users have "salt". A one-shot swap
        # collides here on Postgres; the placeholder pass is what avoids it.
        conn.execute(
            models.Staple.__table__.insert(),
            [
                {"id": 1, "user_id": "1", "household_id": H_LEGACY, "name": "salt"},
                {"id": 2, "user_id": "4", "household_id": H_LEGACY, "name": "salt"},
            ],
        )
        conn.execute(
            models.RecipeParseJob.__table__.insert(),
            [{"id": "job-1", "user_id": "1", "job_type": "url", "status": "COMPLETE", "household_id": H_LEGACY}],
        )
        conn.execute(
            models.RecipeIngestion.__table__.insert(),
            [{"id": "ing-1", "user_id": "1", "image_s3_keys": ["recipe-images/1/ing-1/0.jpg"], "status": "SUCCEEDED"}],
        )
        conn.execute(
            models.MailboxMessage.__table__.insert(),
            [{"id": "m-1", "user_id": "1", "type": "x", "payload": {}}],
        )
        conn.execute(
            models.StageRecipe.__table__.insert(),
            [{"id": 1, "user_id": "4", "title": "Staged", "ingredients": [], "steps": [], "tags": [], "notes": [], "expires_at": datetime(2026, 10, 9)}],
        )
        conn.execute(
            models.GrocerySkuMap.__table__.insert(),
            [{"id": 1, "user_id": "4", "household_id": H_LEGACY, "retailer": "walmart", "ingredient_name": "milk", "sku": "1"}],
        )
        conn.execute(
            models.Setting.__table__.insert(),
            [{"id": 1, "key": "llm.full_model_name", "value": "x", "user_id": 4, "household_id": H_LEGACY}],
        )
    return engine


def _col(engine: Engine, table: Any, col: str, key_col: str = "id") -> dict[Any, Any]:
    with engine.connect() as conn:
        t = table.__table__
        return {r[0]: r[1] for r in conn.execute(select(t.c[key_col], t.c[col]))}


def _users(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(select(models.User.__table__.c.user_id))}


class TestEveryOwnedColumnIsCovered:
    def test_the_column_lists_match_the_models(self):
        """A new user/household column must be added to the script, or this fails."""
        user_cols = {
            (t.name, c.name)
            for t in Base.metadata.sorted_tables
            for c in t.columns
            if c.name == "user_id" and t.name != remap.USERS_TABLE
        }
        household_cols = {(t.name, c.name) for t in Base.metadata.sorted_tables for c in t.columns if c.name == "household_id"}
        assert user_cols == set(remap.USER_COLUMNS)
        assert household_cols == set(remap.HOUSEHOLD_COLUMNS)

    def test_the_integer_user_columns_are_the_integer_ones(self):
        integer = {
            (t.name, c.name)
            for t in Base.metadata.sorted_tables
            for c in t.columns
            if c.name == "user_id" and t.name != remap.USERS_TABLE and c.type.python_type is int
        }
        assert integer == remap.INTEGER_USER_COLUMNS


class TestDryRun:
    def test_reports_and_writes_nothing(self, recipes_db, legacy, jarvisd):
        before = _col(recipes_db, models.Recipe, "user_id")

        plan = remap.run(recipes_db, legacy, jarvisd, apply=False)

        assert plan.user_map == {"1": "2", "4": "1"}
        assert _col(recipes_db, models.Recipe, "user_id") == before
        assert plan.counts["recipes.user_id"] == 2
        out = remap.report(plan, applied=False)
        assert "dry run" in out and "legacy 1 -> jarvisd 2" in out and "legacy 9: no legacy account" in out


class TestApply:
    def test_the_swap_lands_in_every_table(self, recipes_db, legacy, jarvisd):
        plan = remap.run(recipes_db, legacy, jarvisd, apply=True)
        assert plan.errors == []

        assert _col(recipes_db, models.Recipe, "user_id") == {1: "2", 2: "2", 3: "9"}
        assert _col(recipes_db, models.MealPlan, "user_id") == {1: "2", 2: "1"}
        assert _col(recipes_db, models.Staple, "user_id") == {1: "2", 2: "1"}
        assert _col(recipes_db, models.RecipeParseJob, "user_id") == {"job-1": "2"}
        assert _col(recipes_db, models.RecipeIngestion, "user_id") == {"ing-1": "2"}
        assert _col(recipes_db, models.MailboxMessage, "user_id") == {"m-1": "2"}
        assert _col(recipes_db, models.StageRecipe, "user_id") == {1: "1"}
        assert _col(recipes_db, models.GrocerySkuMap, "user_id") == {1: "1"}
        assert _col(recipes_db, models.Setting, "user_id") == {1: 1}
        # The local users mirror follows: 1 and 2 own data, 4 is gone, 9 untouched.
        assert _users(recipes_db) == {"1", "2", "9"}

    def test_households_follow_their_owner(self, recipes_db, legacy, jarvisd):
        plan = remap.run(recipes_db, legacy, jarvisd, apply=True)

        # Home: Alex (legacy admin) is in one jarvisd household. Cabin: Sam owns
        # it and is in two jarvisd households, so the name decides.
        assert plan.household_map == {H_LEGACY: H_NEW, H_LEGACY_OTHER: H_NEW_WORK}
        assert _col(recipes_db, models.Recipe, "household_id") == {1: H_NEW, 2: None, 3: None}
        assert _col(recipes_db, models.MealPlan, "household_id") == {1: H_NEW, 2: H_NEW_WORK}
        assert _col(recipes_db, models.Staple, "household_id") == {1: H_NEW, 2: H_NEW}
        assert _col(recipes_db, models.GrocerySkuMap, "household_id") == {1: H_NEW}
        assert _col(recipes_db, models.Setting, "household_id") == {1: H_NEW}
        assert _col(recipes_db, models.RecipeParseJob, "household_id") == {"job-1": H_NEW}

    def test_unmatched_rows_are_untouched_and_reported(self, recipes_db, legacy, jarvisd):
        plan = remap.run(recipes_db, legacy, jarvisd, apply=True)

        assert ("9", "no legacy account with this id") in plan.unmatched_users
        assert _col(recipes_db, models.Recipe, "user_id")[3] == "9"

    def test_a_second_run_changes_nothing(self, recipes_db, legacy, jarvisd):
        remap.run(recipes_db, legacy, jarvisd, apply=True)
        snapshot = (_col(recipes_db, models.Recipe, "user_id"), _col(recipes_db, models.Staple, "user_id"), _users(recipes_db))

        again = remap.run(recipes_db, legacy, jarvisd, apply=True)

        assert again.user_map == {} and again.household_map == {} and again.changes == 0
        assert (_col(recipes_db, models.Recipe, "user_id"), _col(recipes_db, models.Staple, "user_id"), _users(recipes_db)) == snapshot

    def test_someone_who_signs_up_later_is_picked_up_by_a_rerun(self, recipes_db, legacy):
        only_alex = remap.jarvisd_directory_from([u for u in jarvisd_users() if u["email"] != "sam@example.com"])
        # Alex alone first -- give Alex id 2 so legacy 4 does not collide with anyone.
        plan = remap.run(recipes_db, legacy, only_alex, apply=True)
        assert plan.user_map == {"1": "2"}
        assert ("4", "sam@example.com: no jarvisd account with this email") in plan.unmatched_users
        assert _col(recipes_db, models.StageRecipe, "user_id") == {1: "4"}

        later = remap.jarvisd_directory_from(jarvisd_users() + [
            {"id": 3, "email": "sam2@example.com", "households": []},
        ])
        plan = remap.run(recipes_db, legacy, later, apply=True)
        assert plan.errors == []
        assert plan.user_map == {"4": "1"}
        assert _col(recipes_db, models.StageRecipe, "user_id") == {1: "1"}
        assert _col(recipes_db, models.Recipe, "user_id")[1] == "2", "the first run's rows are not touched again"


class TestRefusals:
    def test_an_unmatched_id_that_is_a_jarvisd_id_is_refused(self, recipes_db, legacy):
        # Sam has not signed up; jarvisd user 4 is someone else entirely.
        users = [u for u in jarvisd_users() if u["email"] != "sam@example.com"]
        users.append({"id": 4, "email": "stranger@example.com", "households": []})
        before = _col(recipes_db, models.Recipe, "user_id")

        plan = remap.run(recipes_db, legacy, remap.jarvisd_directory_from(users), apply=True)

        assert any("same id as jarvisd user 4" in e for e in plan.errors)
        assert _col(recipes_db, models.Recipe, "user_id") == before, "nothing written"

    def test_park_unmatched_moves_them_out_of_the_way(self, recipes_db, legacy):
        users = [u for u in jarvisd_users() if u["email"] != "sam@example.com"]
        users.append({"id": 4, "email": "stranger@example.com", "households": []})

        plan = remap.run(recipes_db, legacy, remap.jarvisd_directory_from(users), apply=True, park_unmatched=True)

        assert plan.errors == []
        assert _col(recipes_db, models.StageRecipe, "user_id") == {1: "legacy-4"}
        # The INTEGER settings column cannot hold a parked id; it keeps 4 and the
        # report says so through the counts (settings.user_id is not listed).
        assert "4" not in _users(recipes_db)

    def test_two_legacy_users_matching_one_account_is_refused(self, recipes_db, tmp_path, jarvisd):
        engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'dup.db'}", future=True)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)"))
            conn.execute(text("CREATE TABLE households (id TEXT PRIMARY KEY, name TEXT)"))
            conn.execute(text("CREATE TABLE household_memberships (id INTEGER PRIMARY KEY, household_id TEXT, user_id INTEGER, role TEXT)"))
            conn.execute(text("INSERT INTO users VALUES (1, 'alex@example.com'), (4, 'ALEX@example.com')"))
        dup = remap.load_legacy(f"sqlite+pysqlite:///{tmp_path / 'dup.db'}")

        plan = remap.run(recipes_db, dup, jarvisd, apply=True)

        assert any("refusing to merge" in e for e in plan.errors)
        assert _col(recipes_db, models.Recipe, "user_id")[1] == "1"


class TestHouseholdMatching:
    def _plan(self, legacy, users, **kw):
        return remap.build_plan(legacy, remap.jarvisd_directory_from(users), {"1", "4"}, {H_LEGACY, H_LEGACY_OTHER}, **kw)

    def test_several_households_narrow_by_name(self, legacy):
        users = jarvisd_users()
        users[1]["households"] = [
            {"household_id": H_NEW_WORK, "household_name": "Work", "role": "admin"},
            {"household_id": H_NEW, "household_name": "home", "role": "admin"},
        ]
        plan = self._plan(legacy, users)
        assert plan.household_map[H_LEGACY] == H_NEW

    def test_still_ambiguous_is_reported_not_guessed(self, legacy):
        users = jarvisd_users()
        users[1]["households"] = [
            {"household_id": H_NEW_WORK, "household_name": "Work", "role": "admin"},
            {"household_id": H_NEW, "household_name": "Flat", "role": "admin"},
        ]
        users[0]["households"] = users[1]["households"]
        plan = self._plan(legacy, users)
        assert H_LEGACY not in plan.household_map
        assert any(h == H_LEGACY and "ambiguous" in why for h, why in plan.unmatched_households)

    def test_an_override_settles_it(self, legacy):
        users = jarvisd_users()
        users[1]["households"] = [
            {"household_id": H_NEW_WORK, "household_name": "Work", "role": "admin"},
            {"household_id": H_NEW, "household_name": "Flat", "role": "admin"},
        ]
        plan = self._plan(legacy, users, overrides={H_LEGACY: H_NEW_WORK, H_LEGACY_OTHER: H_NEW})
        assert plan.household_map == {H_LEGACY: H_NEW_WORK, H_LEGACY_OTHER: H_NEW}
        assert plan.errors == []

    def test_two_legacy_households_onto_one_is_refused(self, legacy):
        # Sam owns the legacy Cabin and is in H_NEW only; Alex's Home -> H_NEW too.
        users = jarvisd_users()
        users[0]["households"] = users[0]["households"][:1]
        plan = self._plan(legacy, users)
        assert any("refusing to merge" in e for e in plan.errors)

    def test_the_admin_decides_not_the_first_member(self, legacy):
        users = jarvisd_users()
        users[0]["households"] = [{"household_id": H_NEW_WORK, "household_name": "Other", "role": "admin"}]
        plan = self._plan(legacy, users)
        assert plan.household_map[H_LEGACY] == H_NEW  # alex (legacy admin) is in H_NEW


def test_jarvisd_shapes_are_read_as_jarvisd_returns_them(monkeypatch):
    calls: list[tuple[str, Any]] = []

    class _Resp:
        def __init__(self, status: int, body: Any) -> None:
            self.status_code, self._body, self.text = status, body, ""

        def json(self) -> Any:
            return self._body

    def post(url: str, json: Any, timeout: float) -> _Resp:
        calls.append((url, json))
        return _Resp(200, {"access_token": "tok", "refresh_token": "r", "token_type": "bearer", "user": {}})

    def get(url: str, headers: dict[str, str], timeout: float) -> _Resp:
        calls.append((url, headers))
        return _Resp(200, jarvisd_users())

    monkeypatch.setattr(remap.httpx, "post", post)
    monkeypatch.setattr(remap.httpx, "get", get)

    token = remap.jarvisd_token("http://jd:7701/", None, "alex@example.com", "pw")
    directory = remap.load_jarvisd("http://jd:7701/", token)

    assert calls[0] == ("http://jd:7701/auth/login", {"email": "alex@example.com", "password": "pw"})
    assert calls[1] == ("http://jd:7701/superuser/users", {"Authorization": "Bearer tok"})
    assert directory.emails == {"1": "sam@example.com", "2": "alex@example.com"}
    assert directory.households["2"] == [(H_NEW, "Home")]
