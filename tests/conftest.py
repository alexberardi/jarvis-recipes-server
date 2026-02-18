import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from jarvis_recipes.app.api.deps import get_db_session, get_storage_provider
from jarvis_recipes.app.core.config import get_settings
from jarvis_recipes.app.db import models  # noqa: F401
from jarvis_recipes.app.db.base import Base
from jarvis_recipes.app.main import create_app
from jarvis_recipes.app.services.storage.local import LocalStorageProvider


@pytest.fixture(scope="session")
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def db_session(engine):
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autocommit=False, autoflush=False, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def app(db_session, tmp_path):
    app = create_app()

    def override_db():
        yield db_session

    def override_storage():
        return LocalStorageProvider(tmp_path)

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_storage_provider] = override_storage
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def auth_settings():
    return get_settings()


def make_token(user_id: int, email: str, settings) -> str:
    payload = {"sub": str(user_id), "email": email}
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.auth_algorithm)


@pytest.fixture
def user_token(auth_settings):
    return make_token(1, "user1@example.com", auth_settings)


@pytest.fixture
def other_user_token(auth_settings):
    return make_token(2, "user2@example.com", auth_settings)

