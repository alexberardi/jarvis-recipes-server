"""Tests for automatic database provisioning.

These exercise the real classification logic in bootstrap.py; only the engine
itself is faked, so the URL parsing, backend detection and pgcode handling all
run for real.
"""

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError

from jarvis_recipes.app.db import bootstrap
from jarvis_recipes.app.db.bootstrap import (
    DUPLICATE_DATABASE,
    INVALID_CATALOG_NAME,
    ensure_database,
)

PG_URL = "postgresql+psycopg2://jarvis:pw@postgres:5432/jarvis_recipes"


class _Orig(Exception):
    def __init__(self, pgcode=None):
        self.pgcode = pgcode


def _op_error(pgcode=None, message="boom"):
    return OperationalError(message, {}, _Orig(pgcode))


class _FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, clause, params=None):
        self._engine.executed.append(str(clause))
        if self._engine.on_execute:
            raise self._engine.on_execute
        return None


class _FakeEngine:
    def __init__(self, url, connect_error=None, on_execute=None):
        self.url = url
        self.connect_error = connect_error
        self.on_execute = on_execute
        self.executed = []
        self.disposed = False
        self.dialect = _Dialect()

    def connect(self):
        if self.connect_error:
            raise self.connect_error
        return _FakeConn(self)

    def dispose(self):
        self.disposed = True


class _Preparer:
    def quote(self, name):
        return f'"{name}"'


class _Dialect:
    identifier_preparer = _Preparer()


@pytest.fixture
def engines(monkeypatch):
    """Records every engine created, keyed by the database it points at."""
    made = {}
    behaviour = {}

    def fake_create_engine(url, **kwargs):
        engine = _FakeEngine(url, **behaviour.get(url.database, {}))
        made[url.database] = engine
        return engine

    monkeypatch.setattr(bootstrap, "create_engine", fake_create_engine)
    made["_behaviour"] = behaviour
    return made


def test_sqlite_is_a_noop(engines):
    assert ensure_database("sqlite+pysqlite:///:memory:") is False
    # Must bail on the backend check alone -- no engine of any kind, since
    # probing a sqlite URL would create the file as a side effect.
    assert ":memory:" not in engines
    assert "postgres" not in engines


def test_existing_database_is_left_alone(engines):
    assert ensure_database(PG_URL) is False
    # Probed the target, never opened a maintenance connection.
    assert "jarvis_recipes" in engines
    assert "postgres" not in engines


def test_missing_database_is_created(engines):
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(INVALID_CATALOG_NAME)
    }

    assert ensure_database(PG_URL) is True

    admin = engines["postgres"]
    assert admin.executed == ['CREATE DATABASE "jarvis_recipes"']
    assert admin.disposed


def test_missing_database_detected_without_pgcode(engines):
    """psycopg2 leaves pgcode unset when the connection itself fails.

    Verified against a real server: connecting to an absent database raises
    OperationalError with pgcode None, so the message match is the branch that
    actually runs in production -- not a fallback for exotic cases.
    """
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(
            None, 'FATAL:  database "jarvis_recipes" does not exist'
        )
    }

    assert ensure_database(PG_URL) is True
    assert engines["postgres"].executed == ['CREATE DATABASE "jarvis_recipes"']


def test_missing_role_is_not_mistaken_for_a_missing_database(engines):
    """A wrong username yields a near-identical message; it must still raise."""
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(None, 'FATAL:  role "jarvis" does not exist')
    }

    with pytest.raises(OperationalError):
        ensure_database(PG_URL)

    assert "postgres" not in engines


def test_message_match_is_anchored_to_this_database(engines):
    """Another database's absence is not this service's problem."""
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(None, 'FATAL:  database "jarvis_ocr" does not exist')
    }

    with pytest.raises(OperationalError):
        ensure_database(PG_URL)


def test_unrelated_connection_error_propagates(engines):
    """A bad password must not be mistaken for a missing database.

    Creating a database that already exists would mask the real misconfiguration
    behind a confusing duplicate error.
    """
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(
            "28P01", "FATAL:  password authentication failed for user"
        )
    }

    with pytest.raises(OperationalError):
        ensure_database(PG_URL)

    assert "postgres" not in engines


def test_concurrent_creation_is_survivable(engines):
    """Services boot together on a fresh stack and race on CREATE DATABASE."""
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(INVALID_CATALOG_NAME)
    }
    engines["_behaviour"]["postgres"] = {
        "on_execute": ProgrammingError("CREATE DATABASE", {}, _Orig(DUPLICATE_DATABASE))
    }

    assert ensure_database(PG_URL) is False


def test_other_creation_failure_propagates(engines):
    """No CREATEDB privilege is a real failure, not a race."""
    engines["_behaviour"]["jarvis_recipes"] = {
        "connect_error": _op_error(INVALID_CATALOG_NAME)
    }
    engines["_behaviour"]["postgres"] = {
        "on_execute": ProgrammingError("CREATE DATABASE", {}, _Orig("42501"))
    }

    with pytest.raises(ProgrammingError):
        ensure_database(PG_URL)


def test_engines_are_always_disposed(engines):
    ensure_database(PG_URL)
    assert engines["jarvis_recipes"].disposed
