import io

import pytest

from jarvis_recipes.app.db import models
from jarvis_recipes.app.services import image_ingest_worker


def _make_upload_file(content: bytes, name: str = "test.jpg"):
    class DummyUpload:
        def __init__(self, data: bytes, filename: str):
            self.file = io.BytesIO(data)
            self.filename = filename

        async def read(self):
            return self.file.read()

    return DummyUpload(content, name)


def _auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


def test_jobs_rejects_more_than_8_images(client, user_token):
    files = [("images", ("f%d.jpg" % i, b"x", "image/jpeg")) for i in range(9)]
    resp = client.post("/recipes/from-image/jobs", files=files, headers=_auth_headers(user_token))
    assert resp.status_code == 400


@pytest.mark.skip(reason="Test needs update for new S3/mailbox integration")
@pytest.mark.asyncio
async def test_happy_path_enqueues_and_emits_mailbox(monkeypatch, client, db_session, user_token):
    # stub S3 upload/download
    monkeypatch.setattr("jarvis_recipes.app.services.s3_storage.upload_image", lambda user_id, ing_id, idx, f: (f"key{idx}", "s3://bucket/key"))
    monkeypatch.setattr("jarvis_recipes.app.services.s3_storage.download_image", lambda key: b"img")

    async def fake_run_pipeline(ingestion, imgs, tier_max):
        draft = type("Draft", (), {"model_dump": lambda self=None: {"title": "ok", "ingredients": [], "steps": []}})
        return draft, {"selected_tier": 1, "attempts": [], "image_count": len(imgs)}, {"tier1_text": "text", "tier2_text": None}

    monkeypatch.setattr("jarvis_recipes.app.services.image_ingest_pipeline.run_ingestion_pipeline", fake_run_pipeline)

    files = [("images", ("f1.jpg", b"x", "image/jpeg"))]
    resp = client.post("/recipes/from-image/jobs", files=files, headers=_auth_headers(user_token))
    assert resp.status_code == 202
    ingestion_id = resp.json()["ingestion_id"]

    job = db_session.query(models.RecipeParseJob).filter(models.RecipeParseJob.job_type == "image").first()
    assert job is not None

    await image_ingest_worker.process_image_ingestion_job(db_session, job)

    msg = db_session.query(models.MailboxMessage).first()
    assert msg is not None
    assert msg.type == "recipe_image_ingestion_completed"
    assert msg.payload["ingestion_id"] == ingestion_id

    ingestion = db_session.get(models.RecipeIngestion, ingestion_id)
    assert ingestion.status == "SUCCEEDED"


@pytest.mark.skip(reason="Test needs update for new S3/mailbox integration")
@pytest.mark.asyncio
async def test_failure_emits_failure_mailbox(monkeypatch, client, db_session, user_token):
    monkeypatch.setattr("jarvis_recipes.app.services.s3_storage.upload_image", lambda user_id, ing_id, idx, f: (f"key{idx}", "s3://bucket/key"))
    monkeypatch.setattr("jarvis_recipes.app.services.s3_storage.download_image", lambda key: b"img")

    async def fake_run_pipeline(ingestion, imgs, tier_max):
        raise RuntimeError("boom")

    monkeypatch.setattr("jarvis_recipes.app.services.image_ingest_pipeline.run_ingestion_pipeline", fake_run_pipeline)

    files = [("images", ("f1.jpg", b"x", "image/jpeg"))]
    resp = client.post("/recipes/from-image/jobs", files=files, headers=_auth_headers(user_token))
    assert resp.status_code == 202
    ingestion_id = resp.json()["ingestion_id"]

    job = db_session.query(models.RecipeParseJob).filter(models.RecipeParseJob.job_type == "image").first()
    assert job is not None

    await image_ingest_worker.process_image_ingestion_job(db_session, job)

    msg = db_session.query(models.MailboxMessage).first()
    assert msg is not None
    assert msg.type == "recipe_image_ingestion_failed"
    assert msg.payload["ingestion_id"] == ingestion_id

    ingestion = db_session.get(models.RecipeIngestion, ingestion_id)
    assert ingestion.status == "FAILED"

