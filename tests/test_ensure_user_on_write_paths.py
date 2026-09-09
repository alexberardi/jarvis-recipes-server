"""Every write path that stores a user_id must create the local user row first.

recipes keeps its own `users` table so recipes, meal plans, ingestions, staged
recipes and mailbox messages can carry a foreign key. jarvis-auth owns identity;
recipes only learns a user exists the first time they write. A path that inserts
a user_id without ensuring the row fails against a fresh database:

    ForeignKeyViolation: insert or update on table "recipe_ingestions" violates
    foreign key constraint "recipe_ingestions_user_id_fkey"
    DETAIL: Key (user_id)=(1) is not present in table "users".

That is a real 500 from prod: photo import worked only for a user who had
already saved a recipe some other way, because saving a recipe ensured the row
and photo import did not. Which features worked depended on the order they were
used in.

These tests use their OWN engine with `PRAGMA foreign_keys=ON`. The shared
fixture leaves SQLite at its default, where foreign keys are silently NOT
enforced -- which is why the whole suite stayed green while this shipped.
"""

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jarvis_recipes.app.db import models
from jarvis_recipes.app.db.base import Base
from jarvis_recipes.app.services import mailbox_service, meal_plan_service
from jarvis_recipes.app.services.user_service import ensure_user

USER_ID = "1"


@pytest.fixture
def fk_db():
    """A session that actually enforces foreign keys, unlike the shared one."""
    # StaticPool: every checkout must be the SAME connection, or each one gets
    # its own empty in-memory database and the tables vanish.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_the_fixture_actually_enforces_foreign_keys(fk_db):
    """Guards the guard: without this the tests below prove nothing."""
    orphan = models.MailboxMessage(
        id="orphan", user_id="999", type="test", payload={}
    )
    fk_db.add(orphan)
    with pytest.raises(Exception) as exc:
        fk_db.commit()
    assert "foreign key" in str(exc.value).lower()


def test_users_table_starts_empty(fk_db):
    assert fk_db.scalars(select(models.User)).all() == []


def test_ensure_user_creates_the_row_once(fk_db):
    first = ensure_user(fk_db, USER_ID)
    second = ensure_user(fk_db, USER_ID)

    assert first.user_id == USER_ID
    assert second.user_id == USER_ID
    assert len(fk_db.scalars(select(models.User)).all()) == 1


def test_ensure_user_accepts_an_int_id(fk_db):
    """CurrentUser.id is an int; the column is a string."""
    user = ensure_user(fk_db, 1)
    assert user.user_id == "1"


def test_publishing_a_mailbox_message_needs_no_prior_recipe(fk_db):
    message = mailbox_service.publish(fk_db, USER_ID, "ocr.completed", {"ok": True})

    assert message.user_id == USER_ID
    assert fk_db.get(models.User, USER_ID) is not None


def test_staging_a_recipe_needs_no_prior_recipe(fk_db):
    stage_id = meal_plan_service.create_stage_recipe(
        fk_db,
        USER_ID,
        {"title": "Crepes", "ingredients": [], "steps": []},
        request_id="req-1",
    )

    assert stage_id is not None
    assert fk_db.get(models.User, USER_ID) is not None


def test_an_ingestion_needs_no_prior_recipe(fk_db):
    """The exact insert that 500'd on prod."""
    ensure_user(fk_db, USER_ID)
    ingestion = models.RecipeIngestion(
        id="ing-1",
        user_id=USER_ID,
        image_s3_keys=["recipe-images/1/ing-1/0.jpg"],
        status="PENDING",
        tier_max=3,
    )
    fk_db.add(ingestion)
    fk_db.commit()

    assert fk_db.get(models.RecipeIngestion, "ing-1") is not None


# --- the actual endpoint that failed on prod -------------------------------


def _tiny_jpeg() -> bytes:
    """A real JPEG; the route decodes uploads before storing them."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def fk_client(fk_db, tmp_path, monkeypatch):
    """The real app, but on a database that enforces foreign keys.

    The shared `client` fixture cannot catch this class of bug: it runs on a
    SQLite session with foreign keys off, so an orphaned user_id inserts
    happily and the endpoint returns 202 whether or not the row exists.
    """
    from fastapi.testclient import TestClient

    from jarvis_recipes.app.api.deps import get_current_user, get_db_session, get_storage_provider
    from jarvis_recipes.app.main import create_app
    from jarvis_recipes.app.schemas.auth import CurrentUser
    from jarvis_recipes.app.services import queue_service, s3_storage
    from jarvis_recipes.app.services.storage.local import LocalStorageProvider

    # Neither Redis nor MinIO is what is under test here; the database insert is.
    monkeypatch.setattr(
        queue_service, "enqueue_ocr_request", lambda **kwargs: 1, raising=True
    )
    monkeypatch.setattr(
        s3_storage,
        "upload_image",
        lambda user_id, ingestion_id, idx, file, data_override=None: (
            f"recipe-images/{user_id}/{ingestion_id}/{idx}.jpg",
            f"s3://jarvis-recipes/recipe-images/{user_id}/{ingestion_id}/{idx}.jpg",
        ),
        raising=True,
    )

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: fk_db
    app.dependency_overrides[get_storage_provider] = lambda: LocalStorageProvider(tmp_path)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=int(USER_ID), email="user1@example.com", household_id=None
    )
    return TestClient(app)


def test_photo_import_works_on_a_brand_new_database(fk_client, fk_db):
    """The prod 500: a fresh install had no users row, so the insert violated its FK.

    Photo import used to require having already saved a recipe some other way,
    because that path created the row as a side effect.
    """
    assert fk_db.scalars(select(models.User)).all() == [], "precondition: no users yet"

    response = fk_client.post(
        "/recipes/from-image/jobs",
        files={"images": ("card.jpg", _tiny_jpeg(), "image/jpeg")},
    )

    assert response.status_code == 202, response.text
    assert fk_db.get(models.User, USER_ID) is not None
