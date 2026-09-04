"""Tests for the settings definitions, the settings service and /settings/*.

The definition-hygiene block exists because three `parser.*` keys shipped with
no reader anywhere in the service: jarvis-admin rendered them as editable knobs
that did nothing. `test_every_definition_has_a_consumer` and
`test_definitions_match_the_seed_migration` are the guards against that
happening again — a new definition has to be seeded and actually read.
"""

import importlib.util
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from jarvis_settings_client import SettingsService
from jarvis_settings_client.types import SettingValue
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jarvis_recipes.app.db.models import Setting
from jarvis_recipes.app.main import create_app
from jarvis_recipes.app.services import settings_service as settings_service_module
from jarvis_recipes.app.services.settings_service import (
    SETTINGS_DEFINITIONS,
    get_settings_service,
    reset_settings_service,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_MIGRATION = REPO_ROOT / "alembic" / "versions" / "b2c3d4e5f6g7_seed_settings.py"
_VERSIONS = REPO_ROOT / "alembic" / "versions"
# Prune-shaped migrations (PHANTOM_SETTINGS / NEW_SETTINGS), in revision order.
PRUNE_MIGRATIONS = [
    _VERSIONS / "c3d4e5f6a7b8_prune_phantom_settings_seed_real_knobs.py",
    _VERSIONS / "d4e5f6a7b8c9_drop_auth_algorithm.py",
]


def _load_migration(path: Path) -> ModuleType:
    """Import a migration by path (alembic's versions/ is not a package)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seeded_rows() -> dict[str, dict[str, Any]]:
    """The system-default rows a freshly migrated DB ends up with."""
    rows = {row["key"]: row for row in _load_migration(SEED_MIGRATION).SETTINGS}
    # Every prune-shaped migration, applied in order. Folding them in by list
    # rather than by name means the next one to drop a dead knob only has to be
    # added here, and this test keeps telling the truth about what a freshly
    # migrated database actually contains.
    for path in PRUNE_MIGRATIONS:
        migration = _load_migration(path)
        for row in migration.PHANTOM_SETTINGS:
            rows.pop(row["key"], None)
        for row in migration.NEW_SETTINGS:
            rows[row["key"]] = row
    return rows


class TestSettingsDefinitions:
    """Hygiene checks on SETTINGS_DEFINITIONS itself."""

    def test_all_definitions_have_required_fields(self):
        for definition in SETTINGS_DEFINITIONS:
            assert definition.key, "Missing key for definition"
            assert definition.category, f"Missing category for {definition.key}"
            assert definition.description, f"Missing description for {definition.key}"
            assert definition.value_type in ("string", "int", "float", "bool", "json"), (
                f"Invalid value_type for {definition.key}: {definition.value_type}"
            )

    def test_no_duplicate_keys(self):
        keys = [d.key for d in SETTINGS_DEFINITIONS]
        assert len(keys) == len(set(keys)), "Duplicate keys in SETTINGS_DEFINITIONS"

    def test_key_format(self):
        for definition in SETTINGS_DEFINITIONS:
            assert "." in definition.key, f"Key should contain dots: {definition.key}"
            assert definition.key == definition.key.lower(), (
                f"Key should be lowercase: {definition.key}"
            )

    def test_expected_settings_exist(self):
        keys = {d.key for d in SETTINGS_DEFINITIONS}
        assert keys == {
            # "auth.algorithm" was dropped 2026-08-23 (migration d4e5f6a7b8c9):
            # verification accepts HS256 and RS256 from an allowlist now, so a
            # single configured algorithm no longer controls anything here.
            "llm.full_model_name",
            "llm.lightweight_model_name",
            "queue.max_retries",
            "parse_job.abandon_minutes",
            "image.max_bytes",
            "scraper.user_agent",
        }

    def test_auth_algorithm_does_not_come_back(self):
        """It gated verification, and verification must accept both algorithms
        during the RS256 migration. Re-adding it would re-introduce a knob that
        can silently break the staged rollout."""
        assert "auth.algorithm" not in {d.key for d in SETTINGS_DEFINITIONS}

    def test_phantom_parser_keys_are_gone(self):
        """parser.* had no reader; it must not come back."""
        keys = {d.key for d in SETTINGS_DEFINITIONS}
        assert not {k for k in keys if k.startswith("parser.")}

    def test_definitions_match_the_seed_migration(self):
        """Every definition is seeded, and the seeded value is its declared default."""
        rows = _seeded_rows()
        assert {d.key for d in SETTINGS_DEFINITIONS} == set(rows), (
            "SETTINGS_DEFINITIONS and the seed migrations have drifted apart"
        )

        for definition in SETTINGS_DEFINITIONS:
            row = rows[definition.key]
            assert row["value_type"] == definition.value_type, definition.key
            assert row["category"] == definition.category, definition.key
            assert row["env_fallback"] == definition.env_fallback, definition.key
            assert row["description"] == definition.description, definition.key
            expected = (
                "true" if definition.default is True
                else "false" if definition.default is False
                else str(definition.default)
            )
            assert row["value"] == expected, (
                f"Seeded value for {definition.key} is {row['value']!r}, "
                f"declared default is {expected!r}"
            )

    def test_every_definition_has_a_consumer(self):
        """A definition nobody reads is a knob in jarvis-admin that does nothing."""
        sources = [
            p.read_text()
            for p in list((REPO_ROOT / "jarvis_recipes").rglob("*.py"))
            + list((REPO_ROOT / "scripts").rglob("*.py"))
            if p.name != "settings_service.py"
        ]
        blob = "\n".join(sources)
        for definition in SETTINGS_DEFINITIONS:
            assert f'"{definition.key}"' in blob, (
                f"{definition.key} is defined but never read — delete it or wire it up"
            )


@pytest.fixture
def service() -> Iterator[SettingsService]:
    """A SettingsService over a throwaway in-memory SQLite settings table."""
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Setting.__table__.create(engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)

    reset_settings_service()
    instance = SettingsService(
        definitions=SETTINGS_DEFINITIONS,
        get_db_session=session_local,
        setting_model=Setting,
    )
    settings_service_module._settings_service = instance
    try:
        yield instance
    finally:
        reset_settings_service()
        engine.dispose()


class TestSettingsServiceCache:

    def test_cache_hit(self, service):
        cache_key = service._make_cache_key("llm.full_model_name")
        service._cache[cache_key] = SettingValue(
            value="cached-model",
            value_type="string",
            requires_reload=False,
            is_secret=False,
            env_fallback="JARVIS_FULL_MODEL_NAME",
            from_db=True,
            cached_at=time.time(),
        )

        assert service.get("llm.full_model_name") == "cached-model"

    def test_cache_expiry(self, service):
        cache_key = service._make_cache_key("queue.max_retries")
        service._cache[cache_key] = SettingValue(
            value=99,
            value_type="int",
            requires_reload=False,
            is_secret=False,
            env_fallback="LLM_RECIPE_QUEUE_MAX_RETRIES",
            from_db=True,
            cached_at=time.time() - 120,  # older than the 60s TTL
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("LLM_RECIPE_QUEUE_MAX_RETRIES", "7")
            assert service.get("queue.max_retries") == 7

    def test_invalidate_cache_clears_everything(self, service):
        service.get("llm.full_model_name")
        service.get("queue.max_retries")
        assert service._cache

        service.invalidate_cache()

        assert service._cache == {}

    def test_db_value_wins_over_env(self, service):
        service.set("llm.full_model_name", "from-db")
        service.invalidate_cache()

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("JARVIS_FULL_MODEL_NAME", "from-env")
            assert service.get("llm.full_model_name") == "from-db"


class TestSettingsServiceFallback:

    def test_env_fallback_when_not_in_db(self, service):
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("RECIPE_IMAGE_MAX_BYTES", "12345")
            assert service.get_int("image.max_bytes", 0) == 12345

    def test_definition_default_when_no_env(self, service):
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("RECIPE_PARSE_JOB_ABANDON_MINUTES", raising=False)
            assert service.get_int("parse_job.abandon_minutes", 0) == 4320

    def test_unknown_key_returns_the_supplied_default(self, service):
        assert service.get("parser.timeout_seconds", "gone") == "gone"


class TestGetSettingsServiceSingleton:

    def test_returns_the_same_instance(self, service):
        assert get_settings_service() is service

    def test_reset_drops_the_instance(self, service):
        reset_settings_service()
        assert settings_service_module._settings_service is None


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient inside jarvis-settings-client's app-ping."""

    response = _FakeResponse(200, {"app_id": "test-app"})

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return type(self).response


@pytest.fixture
def settings_client(service, monkeypatch) -> TestClient:
    """A client over an app whose settings router uses the throwaway service."""
    monkeypatch.setenv("JARVIS_AUTH_BASE_URL", "http://auth.test")
    return TestClient(create_app())


class TestSettingsRoutesAuth:
    """GET accepts combined auth (app creds or superuser JWT); PUT is superuser-only."""

    def test_read_without_credentials_is_401(self, settings_client):
        resp = settings_client.get("/settings/")
        assert resp.status_code == 401

    def test_write_without_credentials_is_401(self, settings_client):
        resp = settings_client.put(
            "/settings/llm.full_model_name", json={"value": "hacked"}
        )
        assert resp.status_code == 401

    def test_read_accepts_app_credentials(self, settings_client, monkeypatch):
        monkeypatch.setattr(
            "jarvis_settings_client.auth.httpx.AsyncClient", _FakeAsyncClient
        )
        resp = settings_client.get(
            "/settings/",
            headers={"X-Jarvis-App-Id": "test-app", "X-Jarvis-App-Key": "test-key"},
        )
        assert resp.status_code == 200
        keys = {s["key"] for s in resp.json()["settings"]}
        assert keys == {d.key for d in SETTINGS_DEFINITIONS}

    def test_write_rejects_app_credentials(self, settings_client, monkeypatch):
        """App creds are enough to read but must never be enough to write."""
        monkeypatch.setattr(
            "jarvis_settings_client.auth.httpx.AsyncClient", _FakeAsyncClient
        )
        resp = settings_client.put(
            "/settings/llm.full_model_name",
            json={"value": "hacked"},
            headers={"X-Jarvis-App-Id": "test-app", "X-Jarvis-App-Key": "test-key"},
        )
        assert resp.status_code == 401
        assert "Authorization header" in resp.json()["detail"]

    def test_write_rejects_a_non_superuser_jwt(self, settings_client, monkeypatch):
        class _FakeClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
                return _FakeResponse(200, {"id": 1, "email": "a@b.c", "is_superuser": False})

        monkeypatch.setattr("jarvis_settings_client.auth.httpx.Client", _FakeClient)
        resp = settings_client.put(
            "/settings/llm.full_model_name",
            json={"value": "hacked"},
            headers={"Authorization": "Bearer user-token"},
        )
        assert resp.status_code == 403

    def test_unknown_key_is_404_not_a_silent_write(self, settings_client, monkeypatch):
        class _FakeClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def get(self, *args: Any, **kwargs: Any) -> _FakeResponse:
                return _FakeResponse(200, {"id": 1, "email": "a@b.c", "is_superuser": True})

        monkeypatch.setattr("jarvis_settings_client.auth.httpx.Client", _FakeClient)
        resp = settings_client.put(
            "/settings/parser.timeout_seconds",
            json={"value": 5},
            headers={"Authorization": "Bearer admin-token"},
        )
        assert resp.status_code == 404


class TestVerificationAcceptsBothAlgorithms:
    """The RS256 migration turned the algorithm from a *setting* into an
    allowlist.

    `auth.algorithm` used to gate verification here, and these tests used to
    assert that. That contract is wrong during a staged migration: a verifier
    must accept HS256 and RS256 simultaneously so tokens minted before the flip
    keep working after it. A single configured algorithm makes the staged
    rollout impossible — it is the one thing that must NOT gate verification.

    What jarvis-auth mints is jarvis-auth's setting. This service only decides
    what it accepts, and it accepts both.
    """

    def test_hs256_token_verifies(self, auth_settings):
        from fastapi.security import HTTPAuthorizationCredentials
        from jose import jwt

        from jarvis_recipes.app.api.deps import get_current_user

        token = jwt.encode(
            {"sub": "1", "email": "user1@example.com"},
            auth_settings.auth_secret_key,
            algorithm="HS256",
        )
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        assert get_current_user(credentials).id == 1

    def test_rs256_token_verifies_against_the_fetched_public_key(self, auth_settings, monkeypatch):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from fastapi.security import HTTPAuthorizationCredentials
        from jose import jwt

        from jarvis_recipes.app.api import deps as deps_module

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        public_pem = (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        monkeypatch.setattr(deps_module, "_public_key_cache", public_pem)

        token = jwt.encode({"sub": "9", "email": "u9@example.com"}, private_pem, algorithm="RS256")
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        assert deps_module.get_current_user(credentials).id == 9

    def test_rs256_without_a_public_key_fails_closed(self, auth_settings, monkeypatch):
        """No key and none fetchable must be a 401, never an accept."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        from jose import jwt

        from jarvis_recipes.app.api import deps as deps_module

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        monkeypatch.setattr(deps_module, "_public_key_cache", None)
        monkeypatch.setattr(deps_module, "_rs256_public_key", lambda: None)

        token = jwt.encode({"sub": "1"}, private_pem, algorithm="RS256")
        with pytest.raises(HTTPException) as exc:
            deps_module.get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            )
        assert exc.value.status_code == 401

    def test_alg_none_is_rejected(self, auth_settings):
        import base64
        import json

        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from jarvis_recipes.app.api.deps import get_current_user

        def b64(obj) -> str:
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

        token = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': '1'})}."
        with pytest.raises(HTTPException) as exc:
            get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
        assert exc.value.status_code == 401

    def test_hs256_signed_with_the_public_key_is_rejected(self, auth_settings, monkeypatch):
        """Algorithm confusion: the dual-accept window's one real hazard."""
        import base64
        import hashlib
        import hmac
        import json

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from jarvis_recipes.app.api import deps as deps_module

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_pem = (
            key.public_key()
            .public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )
        monkeypatch.setattr(deps_module, "_public_key_cache", public_pem)

        def b64(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

        header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = b64(json.dumps({"sub": "1"}).encode())
        sig = b64(
            hmac.new(public_pem.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        )
        with pytest.raises(HTTPException) as exc:
            deps_module.get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=f"{header}.{payload}.{sig}")
            )
        assert exc.value.status_code == 401


class TestRs256PublicKeyFetch:
    """`_rs256_public_key()` — the RS256 path's only external dependency.

    Every other test in this file monkeypatches `_public_key_cache` directly, so
    the fetch itself, its caching and its error handling were never executed.
    That matters more than the line count suggests: this function decides
    whether an RS256 token can be verified at all, and every one of its failure
    modes has to end in a 401 rather than an accept or a 500.

    Note the two failure paths are NOT symmetric. A reachable jarvis-auth that
    answers badly (500, junk body, missing field) is swallowed here and returns
    None. An *undiscoverable* jarvis-auth is not: `service_config.get_auth_url()`
    raises ValueError and this function does not catch it, so the error unwinds
    into `get_current_user`, whose `except (JWTError, ValueError)` turns it into
    the 401. Both fail closed; only one of them fails closed here.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch):
        """The cache is a module global and would otherwise leak across tests."""
        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(deps_module, "_public_key_cache", None)

    @staticmethod
    def _fake_httpx(monkeypatch, *, json_body=None, status_error=None,
                    transport_error=None, json_error=None):
        """Install a stand-in httpx.Client and return the list of fetched URLs."""
        import httpx

        from jarvis_recipes.app.api import deps as deps_module

        calls: list[str] = []

        class _Response:
            def raise_for_status(self):
                if status_error is not None:
                    raise httpx.HTTPStatusError(
                        "boom", request=None, response=None
                    )

            def json(self):
                if json_error is not None:
                    raise json_error
                return json_body

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                calls.append(url)
                if transport_error is not None:
                    raise transport_error
                return _Response()

        monkeypatch.setattr(deps_module.httpx, "Client", _Client)
        return calls

    def test_fetches_the_key_from_the_auth_service(self, monkeypatch):
        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )
        calls = self._fake_httpx(monkeypatch, json_body={"public_key": "PEM-DATA"})

        assert deps_module._rs256_public_key() == "PEM-DATA"
        assert calls == ["http://auth.invalid/auth/public-key"]

    def test_trailing_slash_does_not_double_up(self, monkeypatch):
        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid/"
        )
        calls = self._fake_httpx(monkeypatch, json_body={"public_key": "PEM-DATA"})

        deps_module._rs256_public_key()
        assert calls == ["http://auth.invalid/auth/public-key"]

    def test_the_key_is_fetched_once_and_then_cached(self, monkeypatch):
        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )
        calls = self._fake_httpx(monkeypatch, json_body={"public_key": "PEM-DATA"})

        assert deps_module._rs256_public_key() == "PEM-DATA"
        assert deps_module._rs256_public_key() == "PEM-DATA"
        assert len(calls) == 1, "second call must come from the cache"

    def test_a_cached_key_survives_jarvis_auth_going_down(self, monkeypatch):
        """The stated reason for caching: a RUNNING service keeps verifying."""
        import httpx

        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )
        self._fake_httpx(monkeypatch, json_body={"public_key": "PEM-DATA"})
        assert deps_module._rs256_public_key() == "PEM-DATA"

        # jarvis-auth now refuses every connection.
        self._fake_httpx(monkeypatch, transport_error=httpx.ConnectError("down"))
        assert deps_module._rs256_public_key() == "PEM-DATA"

    @pytest.mark.parametrize(
        "failure",
        [
            "http-500",
            "connection-refused",
            "timeout",
            "malformed-body",
            "no-public_key-field",
            "null-public_key",
        ],
    )
    def test_every_bad_answer_yields_no_key(self, monkeypatch, failure):
        """None, never a partial key and never an exception out of this call."""
        import httpx

        from jarvis_recipes.app.api import deps as deps_module

        kwargs = {
            "http-500": {"status_error": True},
            "connection-refused": {"transport_error": httpx.ConnectError("refused")},
            "timeout": {"transport_error": httpx.ReadTimeout("slow")},
            "malformed-body": {"json_error": ValueError("not json")},
            "no-public_key-field": {"json_body": {}},
            "null-public_key": {"json_body": {"public_key": None}},
        }[failure]

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )
        self._fake_httpx(monkeypatch, **kwargs)

        assert deps_module._rs256_public_key() is None

    def test_a_failed_fetch_is_not_cached(self, monkeypatch):
        """A cold start during an outage must recover once jarvis-auth returns."""
        import httpx

        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )
        self._fake_httpx(monkeypatch, transport_error=httpx.ConnectError("down"))
        assert deps_module._rs256_public_key() is None

        self._fake_httpx(monkeypatch, json_body={"public_key": "PEM-DATA"})
        assert deps_module._rs256_public_key() == "PEM-DATA"

    def test_undiscoverable_auth_service_propagates_rather_than_returning_none(
        self, monkeypatch
    ):
        """Documents the asymmetry: this one is NOT swallowed here.

        `get_auth_url()` is called before the try block, so no widening of the
        except clause can catch it — only moving the call inside the try would.
        `get_current_user` is what converts it to a 401; see the companion test
        below.
        """
        from jarvis_recipes.app.api import deps as deps_module

        def _boom():
            raise ValueError("Cannot discover jarvis-auth")

        monkeypatch.setattr(deps_module.service_config, "get_auth_url", _boom)

        with pytest.raises(ValueError):
            deps_module._rs256_public_key()


class TestRs256FetchFailuresReachTheVerifierAs401:
    """The fetch failures above, exercised through the actual dependency.

    A 500 here would leak the outage to callers as a server error, and an accept
    would be a straight authentication bypass.
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self, monkeypatch):
        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(deps_module, "_public_key_cache", None)

    @staticmethod
    def _rs256_token() -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jose import jwt

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        return jwt.encode({"sub": "1", "email": "u1@example.com"}, private_pem,
                          algorithm="RS256")

    def test_cold_start_during_an_outage_is_a_401(self, auth_settings, monkeypatch):
        import httpx
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from jarvis_recipes.app.api import deps as deps_module

        monkeypatch.setattr(
            deps_module.service_config, "get_auth_url", lambda: "http://auth.invalid"
        )

        class _Client:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, url):
                raise httpx.ConnectError("refused")

        monkeypatch.setattr(deps_module.httpx, "Client", _Client)

        with pytest.raises(HTTPException) as exc:
            deps_module.get_current_user(
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=self._rs256_token()
                )
            )
        assert exc.value.status_code == 401

    def test_undiscoverable_auth_service_is_a_401_not_a_500(
        self, auth_settings, monkeypatch
    ):
        """The ValueError path, end to end."""
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials

        from jarvis_recipes.app.api import deps as deps_module

        def _boom():
            raise ValueError("Cannot discover jarvis-auth")

        monkeypatch.setattr(deps_module.service_config, "get_auth_url", _boom)

        with pytest.raises(HTTPException) as exc:
            deps_module.get_current_user(
                HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=self._rs256_token()
                )
            )
        assert exc.value.status_code == 401



def test_every_get_settings_service_call_site_imports_it():
    """A missing import here is a NameError only the live route would surface.

    `recipes.py` and `from_image.py` both shipped exactly that bug during the
    settings promotion; this is cheaper than an end-to-end test per call site.
    """
    offenders: list[str] = []
    for path in list((REPO_ROOT / "jarvis_recipes").rglob("*.py")) + list(
        (REPO_ROOT / "scripts").rglob("*.py")
    ):
        source = path.read_text()
        if "get_settings_service()" not in source:
            continue
        if path.name == "settings_service.py":
            continue
        if "import get_settings_service" not in source and "    get_settings_service," not in source:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"call get_settings_service() without importing it: {offenders}"


def test_promoted_settings_are_no_longer_pydantic_fields():
    """Two sources of truth for one knob is how they drift."""
    from jarvis_recipes.app.core.config import Settings

    fields = set(Settings.model_fields)
    assert not fields & {
        "llm_full_model_name",
        "llm_lightweight_model_name",
        "llm_recipe_queue_max_retries",
        "recipe_parse_job_abandon_minutes",
        "recipe_image_max_bytes",
        "scraper_user_agent",
        # Verified dead at promotion time — deleted rather than promoted.
        "recipe_ocr_tier_max",
        "recipe_image_s3_presign_ttl_seconds",
    }
