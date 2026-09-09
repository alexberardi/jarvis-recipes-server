"""Create this service's database if it does not already exist.

The Postgres image only runs ``/docker-entrypoint-initdb.d`` when the data
directory is empty (``docker-entrypoint.sh`` gates it on ``$PGDATA/PG_VERSION``).
An install that adds a service later therefore never runs ``init-db.sh`` again,
so the new service's database is silently missing. Each service provisions its
own database at startup instead, which works on fresh and existing installs
alike.

``init-db.sh`` is still the fast path on a first boot; this is the backstop.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)

# Connected to in order to issue CREATE DATABASE; always present on a Postgres
# server and never the target of this call.
MAINTENANCE_DATABASE = "postgres"

# https://www.postgresql.org/docs/current/errcodes-appendix.html
INVALID_CATALOG_NAME = "3D000"  # database does not exist
DUPLICATE_DATABASE = "42P04"  # database already exists


def _pgcode(exc: Exception) -> str | None:
    return getattr(getattr(exc, "orig", None), "pgcode", None)


def _is_missing_database(exc: OperationalError, name: str) -> bool:
    """True when *exc* means "database *name* does not exist" and nothing else.

    Any other connection failure (bad password, wrong role, host down) must
    propagate -- treating those as "missing" would attempt to create a database
    that already exists and bury the real misconfiguration.

    psycopg2 does not populate pgcode when the *connection* itself fails, so in
    practice this falls through to matching the server message. That match is
    deliberately anchored to the database name: a wrong username produces the
    very similar ``role "jarvis" does not exist``, which must not be mistaken
    for a missing database. A server running under a non-English ``lc_messages``
    matches neither branch and raises, which is the safe direction to fail.
    """
    if _pgcode(exc) == INVALID_CATALOG_NAME:
        return True
    return f'database "{name}" does not exist' in str(exc)


def ensure_database(url: str) -> bool:
    """Ensure the database named in *url* exists. Returns True if it was created.

    A no-op for non-Postgres URLs (sqlite in tests) and when the database is
    already reachable -- in the common case this never opens a maintenance
    connection, so a deployment whose role cannot reach the ``postgres``
    database keeps working exactly as before.
    """
    target = make_url(url)

    if not target.get_backend_name().startswith("postgresql"):
        return False
    if not target.database:
        return False

    if _database_exists(target):
        return False

    return _create_database(target)


def _database_exists(target) -> bool:
    engine = create_engine(target, poolclass=NullPool)
    try:
        with engine.connect():
            return True
    except OperationalError as exc:
        if _is_missing_database(exc, target.database):
            return False
        raise
    finally:
        engine.dispose()


def _create_database(target) -> bool:
    name = target.database
    engine = create_engine(
        target.set(database=MAINTENANCE_DATABASE),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        with engine.connect() as conn:
            quoted = engine.dialect.identifier_preparer.quote(name)
            try:
                conn.execute(text(f"CREATE DATABASE {quoted}"))
            except ProgrammingError as exc:
                # Services boot concurrently and race each other on a fresh
                # stack; whoever loses sees 42P04 and can carry on.
                if _pgcode(exc) != DUPLICATE_DATABASE:
                    raise
                logger.info("Database %s already created concurrently", name)
                return False
    finally:
        engine.dispose()

    logger.info("Created database %s", name)
    return True
