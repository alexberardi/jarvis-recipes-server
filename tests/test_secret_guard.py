"""Boot-time guard on placeholder/weak secrets (P2.3).

recipes-server ships ``auth_secret_key="change-me"`` and
``admin_secret="admin-secret"`` defaults. Warn everywhere, but only ABORT boot
when JARVIS_ENV=production — so an existing self-host box on a default keeps
running, while prod refuses a forgeable JWT key / open admin surface.
"""
import logging

import pytest

from jarvis_recipes.app.core.config import Settings, enforce_secret_security

STRONG = "x" * 40


def _settings(**overrides) -> Settings:
    base = {"AUTH_SECRET_KEY": STRONG, "ADMIN_SECRET": STRONG}
    base.update(overrides)
    return Settings(_env_file=None, **base)


class TestInsecureSecrets:
    def test_strong_secrets_ok(self):
        assert _settings().insecure_secrets() == []

    def test_shipped_default_auth_secret_flagged(self):
        assert "AUTH_SECRET_KEY" in _settings(AUTH_SECRET_KEY="change-me").insecure_secrets()

    def test_shipped_default_admin_secret_flagged(self):
        assert "ADMIN_SECRET" in _settings(ADMIN_SECRET="admin-secret").insecure_secrets()

    def test_short_secret_flagged(self):
        assert "AUTH_SECRET_KEY" in _settings(AUTH_SECRET_KEY="short").insecure_secrets()


class TestEnforce:
    def test_prod_with_default_secret_raises(self):
        cfg = _settings(ADMIN_SECRET="admin-secret", JARVIS_ENV="production")
        with pytest.raises(RuntimeError, match="Refusing to start in production"):
            enforce_secret_security(cfg, logging.getLogger("test"))

    def test_dev_with_default_secret_warns_not_raises(self, caplog):
        cfg = _settings(AUTH_SECRET_KEY="change-me", JARVIS_ENV="development")
        with caplog.at_level(logging.WARNING):
            enforce_secret_security(cfg, logging.getLogger("test"))  # must not raise
        assert any("Insecure config" in r.message for r in caplog.records)

    def test_prod_with_strong_secrets_is_silent(self):
        enforce_secret_security(_settings(JARVIS_ENV="production"), logging.getLogger("test"))
