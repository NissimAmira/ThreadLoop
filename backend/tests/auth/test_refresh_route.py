"""End-to-end integration tests for `POST /api/auth/refresh`.

Each test seeds a `users` row + a `refresh_tokens` row directly (no need
to round-trip through a callback — those have their own coverage) and
drives the refresh route. JWKS isn't involved on this route.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
HMAC_KEY = b"test-hmac-key"
JWT_SIGNING_KEY = "test-jwt-signing-key"


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


def _seed_user_and_token(
    pg_url: str,
    *,
    plaintext: str,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a `users` row and a single `refresh_tokens` row hashing to
    `plaintext`. Returns the user id.

    `expires_at` defaults to now+30d. To exercise the expired-token path,
    pass an `expires_at` in the past — we backdate `issued_at` accordingly
    so the `expires_at > issued_at` CHECK constraint stays satisfied.
    """
    user_id = user_id if user_id is not None else uuid.uuid4()
    now = datetime.now(UTC)
    expires = expires_at if expires_at is not None else now + timedelta(days=30)
    # The DB rejects rows where expires_at <= issued_at. For the "already
    # expired" case, set issued_at strictly before expires_at by a small
    # margin and let `now` (which is later than both) drive the route's
    # is_expired() check.
    issued_at = expires - timedelta(seconds=1) if expires <= now else now
    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users "
                    "(id, provider, provider_user_id, email, email_verified, "
                    "display_name, can_sell, can_purchase, created_at, updated_at) "
                    "VALUES (:id, 'google', :sub, :email, true, "
                    "'Test User', false, true, :now, :now) "
                    "ON CONFLICT DO NOTHING"
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
                    "VALUES (:id, :uid, :hash, :issued, :exp, :rev)"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "hash": _hash(plaintext),
                    "issued": issued_at,
                    "exp": expires,
                    "rev": revoked_at,
                },
            )
    finally:
        engine.dispose()
    return user_id


# ----- happy path -----------------------------------------------------------


def _extract_set_cookie_value(response_headers: Any, name: str) -> str | None:
    """Pull the bare value of a `Set-Cookie: name=value; ...` header.

    httpx exposes the cookie jar via `client.cookies`, but multiple cookies
    can collide on `(name, domain)` between the client-set and server-set
    versions when the server's `path` differs. Reading the Set-Cookie
    header directly avoids that ambiguity.
    """
    raw_headers = response_headers.raw if hasattr(response_headers, "raw") else None
    if raw_headers is None:
        # Older httpx exposes via .multi_items()
        items = (
            response_headers.multi_items()
            if hasattr(response_headers, "multi_items")
            else response_headers.items()
        )
    else:
        items = [(k.decode().lower(), v.decode()) for k, v in raw_headers]
    for key, value in items:
        if key.lower() == "set-cookie" and value.startswith(f"{name}="):
            after = value[len(name) + 1 :]
            return after.split(";", 1)[0]
    return None


def test_refresh_rotates_token_and_returns_session(auth_client: TestClient, pg_url: str) -> None:
    plaintext = "the-current-refresh-token"
    user_id = _seed_user_and_token(pg_url, plaintext=plaintext)

    resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": plaintext})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is False
    assert body["access_token"]
    assert body["expires_at"]
    assert body["user"]["id"] == str(user_id)

    # Cookie was rotated — read directly from the Set-Cookie header to avoid
    # the cookie-jar collision with the value we just sent.
    new_cookie = _extract_set_cookie_value(resp.headers, "refresh_token")
    assert new_cookie is not None
    assert new_cookie != plaintext

    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT token_hash, revoked_at FROM refresh_tokens "
                    "WHERE user_id = :uid ORDER BY issued_at"
                ),
                {"uid": user_id},
            ).all()
        assert len(rows) == 2, "rotation must add a new row"
        # Old row revoked, new row active and matches the cookie we got.
        old_hash, old_revoked = rows[0]
        new_hash, new_revoked = rows[1]
        assert old_revoked is not None
        assert new_revoked is None
        assert bytes(old_hash) == _hash(plaintext)
        assert bytes(new_hash) == _hash(new_cookie)
    finally:
        engine.dispose()


# ----- failure paths --------------------------------------------------------


def test_refresh_without_cookie_returns_401(auth_client: TestClient) -> None:
    resp = auth_client.post("/api/auth/refresh")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "no_refresh_token"


def test_refresh_with_unknown_token_returns_401_and_clears_cookie(
    auth_client: TestClient, pg_url: str
) -> None:
    """A token that hashes to nothing in the table → 401 + cookie cleared.

    The cookie clear keeps the client from replaying the bad value forever.
    """
    resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": "totally-fabricated"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_refresh_token"
    # The Set-Cookie unset should be present on the response.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie


def test_refresh_with_expired_token_returns_401(auth_client: TestClient, pg_url: str) -> None:
    plaintext = "expired-refresh-token"
    _seed_user_and_token(
        pg_url,
        plaintext=plaintext,
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": plaintext})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_refresh_token"


def test_refresh_with_revoked_token_triggers_reuse_detection(
    auth_client: TestClient, pg_url: str
) -> None:
    """Reuse detection (RFC 0001 § Failure modes): an already-revoked token
    presented again means either a benign replay OR active token theft. We
    can't distinguish, so we revoke ALL of that user's refresh tokens and
    return 401. The user must re-authenticate from scratch."""
    revoked_plaintext = "previously-rotated"
    user_id = _seed_user_and_token(
        pg_url,
        plaintext=revoked_plaintext,
        revoked_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    # Add a SECOND, currently-active refresh token for the same user — this
    # is what the reuse-detection branch must also revoke. (Models the
    # scenario where the legitimate user already rotated successfully; the
    # attacker is now replaying the previous token.)
    active_plaintext = "the-currently-active-token"
    engine = create_engine(pg_url, future=True)
    now = datetime.now(UTC)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens "
                    "(id, user_id, token_hash, issued_at, expires_at, revoked_at) "
                    "VALUES (:id, :uid, :hash, :now, :exp, NULL)"
                ),
                {
                    "id": uuid.uuid4(),
                    "uid": user_id,
                    "hash": _hash(active_plaintext),
                    "now": now,
                    "exp": now + timedelta(days=30),
                },
            )

        resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": revoked_plaintext})

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "invalid_refresh_token"

        # All of this user's tokens must be revoked now — including the
        # token that was active before reuse was detected.
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT token_hash, revoked_at FROM refresh_tokens WHERE user_id = :uid"),
                {"uid": user_id},
            ).all()
        assert len(rows) == 2
        # Both rows now have a revoked_at timestamp.
        assert all(row[1] is not None for row in rows), (
            "reuse detection must revoke all of the user's refresh tokens"
        )
    finally:
        engine.dispose()


def test_refresh_with_expired_access_jwt_succeeds_via_refresh_cookie(
    auth_client: TestClient, pg_url: str
) -> None:
    """The whole point of refresh: the client's access JWT has expired, but
    it still holds a valid refresh cookie, and the refresh route hands back
    a fresh JWT.

    Construct an expired access JWT just to confirm the refresh route
    doesn't read it (it shouldn't — refresh is cookie-only).
    """
    plaintext = "valid-refresh-token"
    user_id = _seed_user_and_token(pg_url, plaintext=plaintext)

    # Stale access JWT — the refresh path doesn't read this; it only matters
    # if the route mistakenly tried to validate it.
    now_ts = int(datetime.now(UTC).timestamp())
    expired_jwt = jwt.encode(
        {"alg": "HS256"},
        {"sub": str(user_id), "iat": now_ts - 3600, "exp": now_ts - 60, "typ": "access"},
        JWT_SIGNING_KEY,
    )
    if isinstance(expired_jwt, bytes):
        expired_jwt = expired_jwt.decode("ascii")

    resp = auth_client.post(
        "/api/auth/refresh",
        cookies={"refresh_token": plaintext},
        headers={"Authorization": f"Bearer {expired_jwt}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"] != expired_jwt
