"""End-to-end integration tests for `POST /api/auth/logout`.

The route is intentionally idempotent: 204 whether or not a cookie was
sent, whether or not the cookie maps to a row, whether or not the row was
already revoked. The `Set-Cookie` clear is unconditional so every code
path leaves the client without a stale cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app import db as db_module
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
HMAC_KEY = b"test-hmac-key"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def _hash(plaintext: str) -> bytes:
    return hmac.new(HMAC_KEY, plaintext.encode("utf-8"), hashlib.sha256).digest()


@pytest.fixture
def auth_client(pg_url: str) -> Iterator[TestClient]:
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


def _seed_user_and_active_token(pg_url: str, *, plaintext: str) -> uuid.UUID:
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
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens "
                    "(id, user_id, token_hash, issued_at, expires_at, revoked_at) "
                    "VALUES (:id, :uid, :hash, :now, :exp, NULL)"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "hash": _hash(plaintext),
                    "now": now,
                    "exp": now + timedelta(days=30),
                },
            )
    finally:
        engine.dispose()
    return user_id


# ----- happy path -----------------------------------------------------------


def test_logout_revokes_active_token_and_clears_cookie(
    auth_client: TestClient, pg_url: str
) -> None:
    plaintext = "to-be-logged-out"
    user_id = _seed_user_and_active_token(pg_url, plaintext=plaintext)

    resp = auth_client.post("/api/auth/logout", cookies={"refresh_token": plaintext})

    assert resp.status_code == 204
    assert resp.content == b""

    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie

    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT revoked_at FROM refresh_tokens WHERE user_id = :uid"),
                {"uid": user_id},
            ).one()
        assert row[0] is not None, "logout must revoke the active token"
    finally:
        engine.dispose()


# ----- idempotency ----------------------------------------------------------


def test_logout_without_cookie_returns_204(auth_client: TestClient) -> None:
    """No cookie means the user is already effectively logged out. The
    endpoint must still return 204 — the FE doesn't need to special-case
    'no cookie' on its end."""
    resp = auth_client.post("/api/auth/logout")
    assert resp.status_code == 204


def test_logout_with_unknown_token_returns_204(
    auth_client: TestClient,
) -> None:
    """A cookie with a bogus value (forged / from an old environment) doesn't
    match any row. We still return 204 — surfacing 401 here would leak the
    fact that we run lookups against unknown values."""
    resp = auth_client.post("/api/auth/logout", cookies={"refresh_token": "ghost-token-value"})
    assert resp.status_code == 204


def test_logout_twice_is_idempotent(auth_client: TestClient, pg_url: str) -> None:
    plaintext = "double-logout"
    user_id = _seed_user_and_active_token(pg_url, plaintext=plaintext)

    first = auth_client.post("/api/auth/logout", cookies={"refresh_token": plaintext})
    assert first.status_code == 204

    # Second call sends the same (now-stale) cookie value to exercise the
    # "already revoked" branch explicitly.
    second = auth_client.post("/api/auth/logout", cookies={"refresh_token": plaintext})
    assert second.status_code == 204

    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            row_count = conn.execute(
                text(
                    "SELECT count(*) FROM refresh_tokens "
                    "WHERE user_id = :uid AND revoked_at IS NOT NULL"
                ),
                {"uid": user_id},
            ).scalar_one()
        # Single row, single revocation — second call must not double-revoke
        # or insert a phantom row.
        assert row_count == 1
    finally:
        engine.dispose()
