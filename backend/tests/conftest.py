from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    """Spawn a Postgres container for the session and yield its SQLAlchemy URL.

    Skips the test cleanly if Docker isn't reachable, so unit-only runs (e.g.
    CI without Docker) don't break. Tests that need this fixture must be
    marked `pytest.mark.integration`.

    Also injects the container URL into `DATABASE_URL` and invalidates the
    `get_settings()` cache so that `alembic/env.py` (which reads
    `get_settings().database_url`) targets the test container — not the
    developer's local Postgres.
    """
    import os

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        pytest.skip(f"testcontainers not installed: {exc}")

    try:
        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as exc:
        pytest.skip(f"Docker not reachable for Testcontainers: {exc}")

    from app.config import get_settings

    url = container.get_connection_url()
    prior_db_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()

    try:
        yield url
    finally:
        if prior_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior_db_url
        get_settings.cache_clear()
        container.stop()
