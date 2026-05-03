"""End-to-end integration tests for `GET /api/me`.

The route is the smallest possible surface for `require_user`, so these
tests cover the dep's behaviour through TestClient + a real Postgres rather
than re-doing the unit-level coverage in `test_require_user.py`.

Why both: the unit tests pin failure mapping without spinning a container;
the integration tests confirm the dep is wired into FastAPI's `Depends`
machinery correctly (a swap of `Depends(require_user)` for `Depends()` would
silently still 200 in unit-only coverage).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from authlib.jose import jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app import db as db_module
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import User
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def _mint_access_token(user_id: uuid.UUID, *, jwt_signing_key: str, exp_offset: int = 3600) -> str:
    """Mint a token directly so tests don't need to round-trip through a
    callback (those have their own coverage)."""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + exp_offset,
        "typ": "access",
    }
    encoded = jwt.encode({"alg": "HS256"}, payload, jwt_signing_key)
    return encoded.decode("ascii") if isinstance(encoded, bytes) else encoded


@pytest.fixture
def auth_client(pg_url: str) -> Iterator[TestClient]:
    """A TestClient wired to a fresh Postgres + auth-test settings.

    Mirrors `test_google_callback_integration.auth_client` to keep these
    integration suites independent. Truncates state before each test.
    """
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")

    engine = create_engine(pg_url, future=True)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as conn:
        for table in ("refresh_tokens", "users"):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    def override_get_db() -> Iterator[DbSession]:
        session = test_session_local()
        try:
            yield session
        finally:
            session.close()

    test_settings = make_test_settings(
        database_url=pg_url,
        refresh_cookie_secure=False,
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: test_settings
    db_module.engine = engine
    db_module.SessionLocal = test_session_local
    Base.metadata.bind = engine

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_settings, None)
        engine.dispose()


def _seed_user(pg_url: str) -> User:
    """Insert a user row directly so we don't have to run a full callback."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, provider, provider_user_id, email, email_verified, "
                    "display_name, can_sell, can_purchase, created_at, updated_at) "
                    "VALUES (:id, 'google', :sub, :email, true, "
                    "'Test User', false, true, :now, :now)"
                ),
                {
                    "id": user_id,
                    "sub": f"google-sub-{user_id.hex[:8]}",
                    "email": f"user-{user_id.hex[:8]}@example.com",
                    "now": now,
                },
            )
    finally:
        engine.dispose()
    user = User()
    user.id = user_id
    return user


# ----- happy path -----------------------------------------------------------


def test_me_returns_user_for_valid_bearer(auth_client: TestClient, pg_url: str) -> None:
    user = _seed_user(pg_url)
    token = _mint_access_token(user.id, jwt_signing_key="test-jwt-signing-key")

    resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(user.id)
    assert body["provider"] == "google"
    assert body["email"] is not None
    assert body["emailVerified"] is True
    assert body["canSell"] is False
    assert body["canPurchase"] is True


# ----- failure modes --------------------------------------------------------


def test_me_without_token_returns_401(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "not_authenticated"


def test_me_with_expired_token_returns_401(auth_client: TestClient, pg_url: str) -> None:
    user = _seed_user(pg_url)
    expired = _mint_access_token(user.id, jwt_signing_key="test-jwt-signing-key", exp_offset=-60)
    resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_token"


def test_me_with_user_deleted_between_issue_and_use_returns_401(
    auth_client: TestClient, pg_url: str
) -> None:
    """Token signed for a uuid that no longer (or never did) exist in `users`.

    Models 'account deleted, but the access JWT is still in the client's
    memory'. Should 401, not 500 or 404.
    """
    ghost_id = uuid.uuid4()
    token = _mint_access_token(ghost_id, jwt_signing_key="test-jwt-signing-key")
    resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_token"


def test_me_with_non_bearer_scheme_returns_401(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/me", headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_authorization_scheme"


def test_me_with_token_signed_by_different_key_returns_401(
    auth_client: TestClient, pg_url: str
) -> None:
    user = _seed_user(pg_url)
    forged = _mint_access_token(user.id, jwt_signing_key="some-other-secret")
    resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_token"


def test_me_when_auth_disabled_returns_404(auth_client: TestClient, pg_url: str) -> None:
    """RFC 0001 § Rollout plan step 1: while `AUTH_ENABLED=false`, the auth
    surface — including identity look-up — must look like it doesn't exist.
    Mirrors `test_auth_disabled_returns_404` for the Google callback. Without
    the explicit `require_auth_enabled` gate on the users router, this
    request would still 401 (which leaks subsystem presence) instead of
    404."""
    user = _seed_user(pg_url)
    token = _mint_access_token(user.id, jwt_signing_key="test-jwt-signing-key")
    prior = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: make_test_settings(
        auth_enabled=False,
        database_url=pg_url,
        refresh_cookie_secure=False,
    )
    try:
        resp = auth_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = prior
    assert resp.status_code == 404
