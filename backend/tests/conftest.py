"""Tests run against their own database (recon_test), rebuilt from migrations once per run."""

import os
from collections.abc import Iterator
from pathlib import Path

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://recon:recon@localhost:5433/recon_test"
)
# Must be set before anything imports app.config.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SESSION_SECRET"] = "test-only-secret-" + "x" * 40
os.environ["COOKIE_SECURE"] = "false"
os.environ["RUN_WORKER"] = "false"  # tests run uploads themselves
os.environ["AI_ENABLED"] = "false"  # never call a paid API by accident; see test_mapping_eval

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.auth.deps import CSRF_COOKIE, CSRF_HEADER  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.enums import Role  # noqa: E402
from app.main import app  # noqa: E402
from app.schema import metadata, users  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple"


def _create_database_if_missing() -> None:
    url = make_url(get_settings().sqlalchemy_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    _create_database_if_missing()
    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        cfg = Config(str(BACKEND_DIR / "alembic.ini"))
        cfg.attributes["connection"] = conn
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    names = ", ".join(t.name for t in metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user():
    def _make(role: Role, email: str | None = None, active: bool = True) -> dict:
        email = email or f"{role.value}@example.test"
        with get_engine().begin() as conn:
            user_id = conn.execute(
                insert(users)
                .values(
                    email=email,
                    name=f"Test {role.value}",
                    password_hash=hash_password(PASSWORD),
                    role=role.value,
                    is_active=active,
                )
                .returning(users.c.id)
            ).scalar_one()
        return {"id": user_id, "email": email, "password": PASSWORD, "role": role}

    return _make


@pytest.fixture
def signed_in(client: TestClient, make_user):
    """A client signed in as a new user of the given role, sending the CSRF header."""

    def _signed_in(role: Role) -> TestClient:
        user = make_user(role)
        response = client.post(
            "/api/auth/login", json={"email": user["email"], "password": user["password"]}
        )
        assert response.status_code == 200, response.text
        client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
        return client

    return _signed_in
