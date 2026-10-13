"""Shared fixtures for application tests."""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from medical_archive.db import create_database, get_session
from medical_archive.main import create_app
from medical_archive.models import User


class _CsrfTokenParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == "csrf_token":
            self.value = attributes.get("value")


def _extract_csrf_token(document: str) -> str:
    parser = _CsrfTokenParser()
    parser.feed(document)
    if parser.value is None:
        raise AssertionError("The form does not contain a CSRF token.")
    return parser.value


@pytest.fixture
def extract_csrf_token():
    return _extract_csrf_token


@pytest.fixture
def auth_engine(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'auth-test.db').as_posix()}"
    project_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(project_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(project_root / "migrations"))
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")

    engine = create_database(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def auth_session_factory(auth_engine):
    return sessionmaker(bind=auth_engine, expire_on_commit=False)


@pytest.fixture
def auth_db(auth_session_factory):
    with auth_session_factory() as session:
        yield session


@pytest.fixture
def app(auth_session_factory):
    application = create_app(testing=True)

    def override_session():
        with auth_session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_session
    return application


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin_user(auth_db: Session):
    user = User(
        email="admin@example.test",
        password_hash=PasswordHasher().hash("correct horse battery"),
        is_admin=True,
    )
    auth_db.add(user)
    auth_db.commit()
    return user


@pytest.fixture
def member_user(auth_db: Session):
    user = User(
        email="member@example.test",
        password_hash=PasswordHasher().hash("correct horse battery"),
        is_admin=False,
    )
    auth_db.add(user)
    auth_db.commit()
    return user
