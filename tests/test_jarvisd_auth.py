"""Verifying tokens minted by jarvisd.

jarvisd mints RS256 only, names its key with a `kid` header, and publishes
`{"public_key", "algorithm", "kid"}` at /auth/public-key. Nothing on a jarvisd
install sets AUTH_SECRET_KEY, so HS256 must be OFF there -- not verified
against an empty or placeholder secret that anyone could sign with.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from jarvis_recipes.app.api import deps as deps_module
from jarvis_recipes.app.core.config import Settings, enforce_secret_security

STRONG = "y" * 40


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _jarvisd_token(private_pem: str, kid: str | None, **claims: Any) -> str:
    """The claim set jarvisd's auth module mints (internal/modules/auth/keys.go mintAccess)."""
    now = int(time.time())
    payload = {
        "sub": "7",
        "email": "cook@example.com",
        "is_superuser": False,
        "household_id": str(uuid.uuid4()),
        "jti": "abc123",
        "iat": now,
        "exp": now + 900,
    }
    payload.update(claims)
    headers = {"kid": kid} if kid else None
    return jwt.encode(payload, private_pem, algorithm="RS256", headers=headers)


def _verify(token: str):
    return deps_module.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.fixture(autouse=True)
def _fresh_key_cache(monkeypatch):
    monkeypatch.setattr(deps_module, "_public_key_cache", None)
    monkeypatch.setattr(deps_module, "_public_key_kid", None)
    monkeypatch.setattr(deps_module, "_last_forced_refresh", 0.0)


@pytest.fixture
def no_hs256_secret(monkeypatch):
    """A jarvisd install: AUTH_SECRET_KEY never set."""
    monkeypatch.setattr(deps_module, "get_settings", lambda: Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY=""))


class _KeyServer:
    """Stands in for jarvisd's GET /auth/public-key; swap `.body` to rotate."""

    def __init__(self, body: dict[str, Any] | None):
        self.body = body
        self.calls = 0
        self.down = False

    def install(self, monkeypatch) -> None:
        import httpx

        server = self

        class _Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> Any:
                return server.body

        class _Client:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def __enter__(self) -> "_Client":
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

            def get(self, url: str) -> _Response:
                server.calls += 1
                if server.down:
                    raise httpx.ConnectError("down")
                assert url == "http://jarvisd.invalid:7701/auth/public-key"
                return _Response()

        monkeypatch.setattr(deps_module.httpx, "Client", _Client)
        monkeypatch.setattr(deps_module.service_config, "get_auth_url", lambda: "http://jarvisd.invalid:7701")


class TestJarvisdRs256Tokens:
    def test_a_jarvisd_token_verifies_and_carries_the_household(self, monkeypatch, no_hs256_secret):
        private_pem, public_pem = _keypair()
        server = _KeyServer({"public_key": public_pem, "algorithm": "RS256", "kid": "k1"})
        server.install(monkeypatch)
        household = str(uuid.uuid4())

        user = _verify(_jarvisd_token(private_pem, "k1", sub="12", household_id=household))

        assert (user.id, user.email, user.household_id) == (12, "cook@example.com", household)
        assert server.calls == 1

    def test_a_token_from_a_new_key_refetches_once(self, monkeypatch, no_hs256_secret):
        """The cutover case: the cached key is the legacy one, the token is jarvisd's."""
        old_private, old_public = _keypair()
        new_private, new_public = _keypair()
        server = _KeyServer({"public_key": old_public})  # legacy shape: no kid
        server.install(monkeypatch)
        assert _verify(_jarvisd_token(old_private, None)).id == 7

        server.body = {"public_key": new_public, "algorithm": "RS256", "kid": "new"}
        assert _verify(_jarvisd_token(new_private, "new")).id == 7
        assert server.calls == 2

        # And it is cached under the new kid: no further fetches.
        _verify(_jarvisd_token(new_private, "new"))
        assert server.calls == 2

    def test_unknown_kids_cannot_hammer_the_auth_service(self, monkeypatch, no_hs256_secret):
        private_pem, public_pem = _keypair()
        attacker_private, _ = _keypair()
        server = _KeyServer({"public_key": public_pem, "kid": "k1"})
        server.install(monkeypatch)
        _verify(_jarvisd_token(private_pem, "k1"))

        for i in range(5):
            with pytest.raises(HTTPException) as exc:
                _verify(_jarvisd_token(attacker_private, f"junk-{i}"))
            assert exc.value.status_code == 401
        # One forced refresh in the window, not five.
        assert server.calls == 2

    def test_a_failed_refresh_keeps_the_key_that_works(self, monkeypatch, no_hs256_secret):
        private_pem, public_pem = _keypair()
        server = _KeyServer({"public_key": public_pem, "kid": "k1"})
        server.install(monkeypatch)
        _verify(_jarvisd_token(private_pem, "k1"))

        server.down = True
        monkeypatch.setattr(deps_module, "_last_forced_refresh", 0.0)
        # A different kid forces a refresh, which fails; the cached key stays.
        _verify(_jarvisd_token(private_pem, "other"))
        assert deps_module._public_key_cache == public_pem
        assert _verify(_jarvisd_token(private_pem, "k1")).id == 7


class TestHs256IsOffWithoutASecret:
    def test_hs256_is_rejected_when_no_secret_is_configured(self, no_hs256_secret):
        token = jwt.encode({"sub": "1"}, "", algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            _verify(token)
        assert exc.value.status_code == 401

    @pytest.mark.parametrize("placeholder", ["change-me", "secret", "short"])
    def test_hs256_signed_with_a_placeholder_is_rejected(self, monkeypatch, placeholder):
        monkeypatch.setattr(
            deps_module,
            "get_settings",
            lambda: Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY=placeholder),
        )
        token = jwt.encode({"sub": "1"}, placeholder, algorithm="HS256")
        with pytest.raises(HTTPException) as exc:
            _verify(token)
        assert exc.value.status_code == 401

    def test_hs256_still_verifies_with_a_real_secret(self, monkeypatch):
        """The legacy stack's tokens, while it is still running."""
        monkeypatch.setattr(
            deps_module, "get_settings", lambda: Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY=STRONG)
        )
        assert _verify(jwt.encode({"sub": "3"}, STRONG, algorithm="HS256")).id == 3


class TestSecretGuardOnJarvisd:
    def test_an_unset_auth_secret_is_not_a_problem(self):
        assert Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY="").insecure_secrets() == []

    def test_production_starts_without_an_auth_secret(self):
        cfg = Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY="", JARVIS_ENV="production")
        enforce_secret_security(cfg, logging.getLogger("test"))  # must not raise

    def test_a_weak_auth_secret_that_is_set_is_still_flagged(self):
        cfg = Settings(_env_file=None, ADMIN_SECRET=STRONG, AUTH_SECRET_KEY="change-me")
        assert cfg.insecure_secrets() == ["AUTH_SECRET_KEY"]
