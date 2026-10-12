"""SQLAlchemy 2.1 defaults ``postgresql://`` to psycopg v3, which isn't installed."""

import pytest

from jarvis_recipes.app.core.config import Settings, pin_psycopg2


def test_bare_url_gets_psycopg2():
    assert pin_psycopg2("postgresql://u:p@h/db") == "postgresql+psycopg2://u:p@h/db"


def test_explicit_driver_is_kept():
    assert pin_psycopg2("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_sqlite_url_is_kept():
    assert pin_psycopg2("sqlite:///./jarvis.db") == "sqlite:///./jarvis.db"


def test_settings_pins_bare_database_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h:5432/jarvis_recipes")
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+psycopg2://u:p@h:5432/jarvis_recipes"
