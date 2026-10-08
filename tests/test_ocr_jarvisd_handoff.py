"""Photo import OCR over jarvisd's HTTP job API.

Submit: POST {ocr}/v1/ocr/jobs with the images inline and a callback_url.
Return: jarvisd POSTs the `ocr.completed` envelope to /internal/ocr/callback,
signed with its app credentials; we queue it on our own RQ queue unchanged.

The envelope fixture below is jarvisd's completionMessage (internal/modules/ocr/
jobs.go) field for field, so a change on either side shows up here.
"""
from __future__ import annotations

import base64
import io
import json
import uuid
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from jarvis_recipes.app.api import deps as deps_module
from jarvis_recipes.app.core.config import Settings
from jarvis_recipes.app.db import models
from jarvis_recipes.app.schemas.ingestion import RecipeDraft
from jarvis_recipes.app.services import (
    ocr_dispatch,
    ocr_jobs_client,
    queue_service,
    queue_worker,
    s3_storage,
)
from jarvis_recipes.app.services.ocr_jobs_client import OcrImage, OcrSubmitError
from tests.fixtures.ocr_samples import CREPE_CARD_APPLE_VISION

APP_HEADERS = {"X-Jarvis-App-Id": "jarvisd", "X-Jarvis-App-Key": "jarvisd-key"}


def _settings(**env: str) -> Settings:
    base = {"JARVIS_APP_ID": "jarvis-recipes-server", "JARVIS_APP_KEY": "recipes-key", "ADMIN_SECRET": "z" * 40}
    base.update(env)
    return Settings(_env_file=None, **base)


def jarvisd_envelope(workflow_id: str, ocr_job_id: str, text: str = CREPE_CARD_APPLE_VISION) -> dict[str, Any]:
    """What jarvisd's ocr.callback handler POSTs for a successful flow job."""
    return {
        "schema_version": 1,
        "job_id": str(uuid.uuid4()),
        "workflow_id": workflow_id,
        "job_type": "ocr.completed",
        "source": "jarvis-ocr-service",
        "target": "jarvis-recipes-server",
        "created_at": "2026-10-08T12:00:00.000000Z",
        "attempt": 1,
        "reply_to": None,
        "payload": {
            "status": "success",
            "results": [
                {
                    "index": 0,
                    "ocr_text": text,
                    "truncated": False,
                    "meta": {
                        "language": "en",
                        "confidence": 0.91,
                        "text_len": len(text),
                        "is_valid": True,
                        "tier": "tesseract",
                        "validation_reason": None,
                    },
                    "error": None,
                }
            ],
            "artifact_ref": None,
            "error": {"message": None, "code": None},
        },
        "trace": {"request_id": None, "parent_job_id": workflow_id},
        "ocr_job_id": ocr_job_id,
    }


class _FakeHttp:
    """httpx.Client stand-in recording POSTs and answering with a canned response."""

    def __init__(self, status_code: int = 202, body: Any = None, error: Exception | None = None):
        self.status_code = status_code
        self.body = body if body is not None else {"job_id": "ocr-job-1", "status": "pending", "created_at": "x"}
        self.error = error
        self.posts: list[dict[str, Any]] = []

    def install(self, monkeypatch) -> None:
        fake = self

        class _Client:
            def __init__(self, *a: Any, **kw: Any) -> None:
                pass

            def __enter__(self) -> "_Client":
                return self

            def __exit__(self, *a: Any) -> bool:
                return False

            def post(self, url: str, json: Any = None, headers: Any = None) -> httpx.Response:
                fake.posts.append({"url": url, "json": json, "headers": headers})
                if fake.error:
                    raise fake.error
                return httpx.Response(fake.status_code, json=fake.body, request=httpx.Request("POST", url))

        monkeypatch.setattr(ocr_jobs_client.httpx, "Client", _Client)


@pytest.fixture
def discovery(monkeypatch):
    monkeypatch.setattr(ocr_jobs_client.service_config, "get_ocr_url", lambda: "http://jarvisd.invalid:7031")
    monkeypatch.setattr(ocr_jobs_client.service_config, "get_recipes_url", lambda: "http://recipes.invalid:7030")
    monkeypatch.setattr(ocr_jobs_client, "get_settings", lambda: _settings())


class TestSubmitJob:
    def test_posts_the_images_and_the_callback_to_jarvisd(self, monkeypatch, discovery):
        http = _FakeHttp()
        http.install(monkeypatch)

        job_id = ocr_jobs_client.submit_job([OcrImage(b"\xff\xd8jpeg")], workflow_id="job-9")

        assert job_id == "ocr-job-1"
        (post,) = http.posts
        assert post["url"] == "http://jarvisd.invalid:7031/v1/ocr/jobs"
        assert post["headers"] == {"X-Jarvis-App-Id": "jarvis-recipes-server", "X-Jarvis-App-Key": "recipes-key"}
        body = post["json"]
        assert body["images"] == [{"content_type": "image/jpeg", "base64": base64.b64encode(b"\xff\xd8jpeg").decode()}]
        assert body["callback_url"] == "http://recipes.invalid:7030/internal/ocr/callback"
        assert body["workflow_id"] == body["parent_job_id"] == "job-9"
        assert body["source"] == "jarvis-recipes-server"
        assert body["options"] == {"language": "en"}

    def test_recipes_public_url_overrides_discovery(self, monkeypatch, discovery):
        monkeypatch.setattr(ocr_jobs_client, "get_settings", lambda: _settings(RECIPES_PUBLIC_URL="https://r.example/"))
        http = _FakeHttp()
        http.install(monkeypatch)

        ocr_jobs_client.submit_job([OcrImage(b"x")], workflow_id="w")

        assert http.posts[0]["json"]["callback_url"] == "https://r.example/internal/ocr/callback"

    @pytest.mark.parametrize(
        "fake",
        [
            _FakeHttp(status_code=401, body={"detail": "Invalid app credentials"}),
            _FakeHttp(status_code=503, body={"detail": "Queue service unavailable"}),
            _FakeHttp(status_code=202, body={"status": "pending"}),
            _FakeHttp(error=httpx.ConnectError("refused")),
        ],
        ids=["401", "503", "202-without-job-id", "unreachable"],
    )
    def test_every_failure_is_an_ocr_submit_error(self, monkeypatch, discovery, fake):
        fake.install(monkeypatch)
        with pytest.raises(OcrSubmitError):
            ocr_jobs_client.submit_job([OcrImage(b"x")], workflow_id="w")

    def test_undiscoverable_ocr_is_an_ocr_submit_error(self, monkeypatch, discovery):
        def boom() -> str:
            raise ValueError("Cannot discover jarvis-ocr-service")

        monkeypatch.setattr(ocr_jobs_client.service_config, "get_ocr_url", boom)
        with pytest.raises(OcrSubmitError, match="Cannot discover"):
            ocr_jobs_client.submit_job([OcrImage(b"x")], workflow_id="w")

    def test_missing_app_credentials_refuse_before_any_request(self, monkeypatch, discovery):
        monkeypatch.setattr(ocr_jobs_client, "get_settings", lambda: _settings(JARVIS_APP_ID="", JARVIS_APP_KEY=""))
        http = _FakeHttp()
        http.install(monkeypatch)
        with pytest.raises(OcrSubmitError, match="JARVIS_APP_ID"):
            ocr_jobs_client.submit_job([OcrImage(b"x")], workflow_id="w")
        assert http.posts == []

    def test_more_than_eight_images_is_refused(self, discovery):
        with pytest.raises(OcrSubmitError):
            ocr_jobs_client.submit_job([OcrImage(b"x")] * 9, workflow_id="w")


@pytest.fixture
def image_job(db_session):
    db_session.add(models.User(user_id="1"))
    ingestion = models.RecipeIngestion(id="ing-1", user_id="1", image_s3_keys=["k"], status="PENDING")
    job = models.RecipeParseJob(
        id="job-1", user_id="1", job_type="image", status="PENDING", job_data={"ingestion_id": "ing-1", "tier_max": 3}
    )
    db_session.add_all([ingestion, job])
    db_session.commit()
    return ingestion, job


def _transport(monkeypatch, value: str) -> None:
    monkeypatch.setattr(ocr_dispatch, "transport", lambda: value)


class TestDispatch:
    def test_http_records_jarvisds_job_id_and_expects_one_reading(self, monkeypatch, db_session, image_job):
        _, job = image_job
        _transport(monkeypatch, "http")
        monkeypatch.setattr(ocr_jobs_client, "submit_job", lambda images, workflow_id, language: "ocr-77")

        assert ocr_dispatch.dispatch(db_session, job, [OcrImage(b"x")], []) == 1
        db_session.refresh(job)
        assert job.job_data["ocr_job_id"] == "ocr-77"
        assert job.job_data["ingestion_id"] == "ing-1", "the existing job data is kept"

    def test_redis_is_the_legacy_fan_out(self, monkeypatch, db_session, image_job):
        _, job = image_job
        _transport(monkeypatch, "redis")
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(queue_service, "enqueue_ocr_request", lambda **kw: calls.append(kw) or 2)

        assert ocr_dispatch.dispatch(db_session, job, [OcrImage(b"x")], [{"kind": "s3"}]) == 2
        assert calls[0]["workflow_id"] == "job-1"

    def test_an_unknown_transport_is_refused(self, monkeypatch, db_session, image_job):
        _, job = image_job
        _transport(monkeypatch, "carrier-pigeon")
        with pytest.raises(OcrSubmitError):
            ocr_dispatch.dispatch(db_session, job, [OcrImage(b"x")], [])

    def test_the_setting_defaults_to_http(self, monkeypatch):
        class _Svc:
            def get_str(self, key: str, default: str) -> str:
                assert key == "ocr.transport"
                return default

        monkeypatch.setattr(ocr_dispatch, "get_settings_service", lambda: _Svc())
        assert ocr_dispatch.transport() == "http"


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def no_s3(monkeypatch):
    monkeypatch.setattr(
        s3_storage,
        "upload_image",
        lambda user_id, ingestion_id, idx, file, data_override=None: (f"k{idx}", f"s3://b/k{idx}"),
    )


class TestFromImageRoute:
    def test_a_photo_import_is_submitted_to_jarvisd(self, monkeypatch, client, db_session, user_token, no_s3):
        _transport(monkeypatch, "http")
        sent: list[list[OcrImage]] = []
        monkeypatch.setattr(
            ocr_jobs_client, "submit_job", lambda images, workflow_id, language: sent.append(images) or "ocr-5"
        )

        resp = client.post(
            "/recipes/from-image/jobs",
            files={"images": ("card.jpg", _jpeg(), "image/jpeg")},
            headers={"Authorization": f"Bearer {user_token}"},
        )

        assert resp.status_code == 202, resp.text
        job = db_session.get(models.RecipeParseJob, resp.json()["job_id"])
        assert job.job_data["ocr_job_id"] == "ocr-5"
        assert db_session.get(models.RecipeIngestion, resp.json()["ingestion_id"]).ocr_expected == 1
        (images,) = sent
        assert images[0].content_type == "image/jpeg" and images[0].data[:2] == b"\xff\xd8"

    def test_a_refused_submit_fails_the_job_with_a_502(self, monkeypatch, client, db_session, user_token, no_s3):
        _transport(monkeypatch, "http")

        def refuse(images: Any, workflow_id: str, language: str) -> str:
            raise OcrSubmitError("OCR service unreachable")

        monkeypatch.setattr(ocr_jobs_client, "submit_job", refuse)

        resp = client.post(
            "/recipes/from-image/jobs",
            files={"images": ("card.jpg", _jpeg(), "image/jpeg")},
            headers={"Authorization": f"Bearer {user_token}"},
        )

        assert resp.status_code == 502
        job = db_session.query(models.RecipeParseJob).filter_by(job_type="image").one()
        assert (job.status, job.error_code) == ("ERROR", "ocr_submit_failed")


@pytest.fixture
def app_ping(monkeypatch):
    """jarvisd's /internal/app-ping: 200 for jarvisd's own credentials, 401 otherwise."""
    seen: list[dict[str, str]] = []

    class _AsyncClient:
        def __init__(self, *a: Any, **kw: Any) -> None:
            pass

        async def __aenter__(self) -> "_AsyncClient":
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            seen.append({"url": url, **headers})
            ok = headers == APP_HEADERS
            return httpx.Response(200 if ok else 401, json={"app_id": "jarvisd", "name": "jarvisd"} if ok else {})

    monkeypatch.setattr(deps_module.httpx, "AsyncClient", _AsyncClient)
    monkeypatch.setattr(deps_module.service_config, "get_auth_url", lambda: "http://jarvisd.invalid:7701")
    return seen


@pytest.fixture
def queued(monkeypatch):
    out: list[dict[str, Any]] = []
    monkeypatch.setattr(queue_service, "enqueue_completion_envelope", lambda env: out.append(env) or True)
    return out


@pytest.fixture
def submitted_job(db_session, image_job):
    ingestion, job = image_job
    job.job_data = {**job.job_data, "ocr_job_id": "ocr-1"}
    db_session.commit()
    return ingestion, job


class TestCallbackRoute:
    def test_without_app_credentials_it_is_a_401(self, client, submitted_job, queued):
        resp = client.post("/internal/ocr/callback", json=jarvisd_envelope("job-1", "ocr-1"))
        assert resp.status_code == 401
        assert queued == []

    def test_bad_app_credentials_are_a_401(self, client, submitted_job, app_ping, queued):
        resp = client.post(
            "/internal/ocr/callback",
            json=jarvisd_envelope("job-1", "ocr-1"),
            headers={"X-Jarvis-App-Id": "jarvisd", "X-Jarvis-App-Key": "wrong"},
        )
        assert resp.status_code == 401
        assert queued == []

    def test_a_user_jwt_does_not_open_it(self, client, submitted_job, app_ping, queued, user_token):
        resp = client.post(
            "/internal/ocr/callback",
            json=jarvisd_envelope("job-1", "ocr-1"),
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 401

    def test_jarvisds_completion_is_queued_unchanged(self, client, submitted_job, app_ping, queued):
        envelope = jarvisd_envelope("job-1", "ocr-1")
        resp = client.post("/internal/ocr/callback", json=envelope, headers=APP_HEADERS)

        assert resp.status_code == 202, resp.text
        assert resp.json() == {"status": "queued"}
        assert queued == [envelope]
        assert app_ping[0]["url"] == "http://jarvisd.invalid:7701/internal/app-ping"

    @pytest.mark.parametrize(
        "workflow_id, ocr_job_id",
        [("no-such-job", "ocr-1"), ("job-1", "someone-elses-ocr-job")],
        ids=["unknown-job", "ocr-job-mismatch"],
    )
    def test_a_completion_for_a_job_we_did_not_submit_is_a_404(
        self, client, submitted_job, app_ping, queued, workflow_id, ocr_job_id
    ):
        resp = client.post(
            "/internal/ocr/callback", json=jarvisd_envelope(workflow_id, ocr_job_id), headers=APP_HEADERS
        )
        assert resp.status_code == 404
        assert queued == []

    def test_a_redelivery_after_the_job_finished_is_acknowledged_not_rerun(
        self, client, db_session, submitted_job, app_ping, queued
    ):
        _, job = submitted_job
        job.status = "COMPLETE"
        db_session.commit()

        resp = client.post("/internal/ocr/callback", json=jarvisd_envelope("job-1", "ocr-1"), headers=APP_HEADERS)

        assert resp.status_code == 202
        assert resp.json() == {"status": "ignored"}
        assert queued == []

    def test_redis_down_is_a_503_so_jarvisd_retries(self, monkeypatch, client, submitted_job, app_ping):
        def down(env: dict[str, Any]) -> bool:
            raise ConnectionError("redis down")

        monkeypatch.setattr(queue_service, "enqueue_completion_envelope", down)
        resp = client.post("/internal/ocr/callback", json=jarvisd_envelope("job-1", "ocr-1"), headers=APP_HEADERS)
        assert resp.status_code == 503

    def test_something_that_is_not_a_completion_is_a_422(self, client, submitted_job, app_ping, queued):
        envelope = jarvisd_envelope("job-1", "ocr-1")
        envelope["job_type"] = "recipe.import.url.requested"
        resp = client.post("/internal/ocr/callback", json=envelope, headers=APP_HEADERS)
        assert resp.status_code == 422
        assert queued == []


class TestEnqueueCompletionEnvelope:
    def test_queues_the_worker_entry_point_under_the_completion_id(self, monkeypatch):
        enqueued: list[tuple[Any, ...]] = []

        class _Queue:
            def enqueue(self, fn: str, payload: str, job_id: str, job_timeout: str) -> None:
                enqueued.append((fn, json.loads(payload), job_id))

        monkeypatch.setattr(queue_service, "get_redis_connection", lambda: object())
        monkeypatch.setattr(queue_service, "get_queue", lambda name: _Queue())
        monkeypatch.setattr(queue_service.Job, "exists", staticmethod(lambda job_id, connection: False))
        envelope = jarvisd_envelope("job-1", "ocr-1")

        assert queue_service.enqueue_completion_envelope(envelope) is True
        assert enqueued == [("jarvis_recipes.app.services.queue_worker.process_job", envelope, envelope["job_id"])]

    def test_a_redelivery_still_in_the_queue_is_not_queued_twice(self, monkeypatch):
        monkeypatch.setattr(queue_service, "get_redis_connection", lambda: object())
        monkeypatch.setattr(queue_service.Job, "exists", staticmethod(lambda job_id, connection: True))
        monkeypatch.setattr(queue_service, "get_queue", lambda name: pytest.fail("must not enqueue"))

        assert queue_service.enqueue_completion_envelope(jarvisd_envelope("job-1", "ocr-1")) is False


class _NoCloseSession:
    """Hands process_job the test session without letting it close it."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    def close(self) -> None:
        return None


def test_the_worker_turns_a_jarvisd_completion_into_a_draft(db_session, submitted_job):
    """The envelope as jarvisd sends it, through process_job, to a stored draft."""
    ingestion, job = submitted_job

    async def structure(text: str, model: str, readings: Any = None) -> RecipeDraft:
        assert "crepe" in text.lower()
        return RecipeDraft(
            title="Crepes",
            ingredients=[{"name": n, "quantity": "1", "unit": "cup"} for n in ("flour", "milk", "eggs")],
            steps=["Whisk everything together until smooth.", "Cook thin rounds in a hot buttered pan."],
            source={"type": "ocr"},
        )

    with patch.object(queue_worker, "SessionLocal", lambda: _NoCloseSession(db_session)), patch.object(
        queue_worker, "call_text_structuring", side_effect=structure
    ), patch.object(queue_worker, "clean_and_validate_draft", side_effect=lambda d, m: d), patch.object(
        queue_worker.queue_service, "schedule_join_deadline"
    ) as deadline:
        queue_worker.process_job(json.dumps(jarvisd_envelope("job-1", "ocr-1")))

    db_session.refresh(job)
    db_session.refresh(ingestion)
    assert job.status == "COMPLETE", (job.error_code, job.error_message)
    assert job.result_json["recipe_draft"]["title"] == "Crepes"
    assert ingestion.ocr_readings[0]["provider"] == "tesseract"
    deadline.assert_not_called()  # one reading from one jarvisd: nothing to wait for
