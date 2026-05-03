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
    assert body["linkRequired"] is False
    assert body["accessToken"]
    assert body["expiresAt"]
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
    auth_client: TestClient, pg_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A token that hashes to nothing in the table → 401 + cookie cleared.

    The cookie clear keeps the client from replaying the bad value forever.
    """
    import logging as _logging

    caplog.set_level(_logging.INFO, logger="app.routers.auth")
    resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": "totally-fabricated"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_refresh_token"
    # The Set-Cookie unset should be present on the response.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    # Pin the differentiated log line so a regression that swallows the
    # reason (or accidentally leaks token contents) is caught.
    assert any("hash_not_found" in record.getMessage() for record in caplog.records)


def test_refresh_with_expired_token_returns_401(
    auth_client: TestClient, pg_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    import logging as _logging

    plaintext = "expired-refresh-token"
    _seed_user_and_token(
        pg_url,
        plaintext=plaintext,
        expires_at=datetime.now(UTC) - timedelta(seconds=10),
    )
    with caplog.at_level(_logging.INFO, logger="app.routers.auth"):
        resp = auth_client.post("/api/auth/refresh", cookies={"refresh_token": plaintext})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_refresh_token"
    # The `clear_cookie=True` branch in the production code is load-bearing —
    # a regression that flipped it would silently let clients keep replaying
    # the dead token. Pin it via the Set-Cookie header.
    assert "refresh_token=" in resp.headers.get("set-cookie", "")
    # Differentiated INFO log so ops can grep "token_expired".
    assert any("token_expired" in record.getMessage() for record in caplog.records)


def test_refresh_with_revoked_token_triggers_reuse_detection(
    auth_client: TestClient, pg_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Reuse detection (RFC 0001 § Failure modes): an already-revoked token
    presented again means either a benign replay OR active token theft. We
    can't distinguish, so we revoke ALL of that user's refresh tokens and
    return 401. The user must re-authenticate from scratch."""
    import logging as _logging

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

        with caplog.at_level(_logging.WARNING, logger="app.routers.auth"):
            resp = auth_client.post(
                "/api/auth/refresh", cookies={"refresh_token": revoked_plaintext}
            )

        assert resp.status_code == 401
        assert resp.json()["detail"]["code"] == "invalid_refresh_token"
        # Reuse detection MUST clear the refresh cookie. Without this clear,
        # a victim's browser would keep replaying the now-revoked token
        # forever — load-bearing for client correctness, so pin it
        # explicitly. Most important Set-Cookie assertion of the bunch.
        assert "refresh_token=" in resp.headers.get("set-cookie", "")
        # Enriched WARNING carries user_id, issued_at, and age — pin all
        # three so a regression that drops any of them surfaces here.
        warning_messages = [
            record.getMessage() for record in caplog.records if record.levelno == _logging.WARNING
        ]
        assert any("Refresh-token reuse detected" in m for m in warning_messages)
        assert any(f"user_id={user_id}" in m for m in warning_messages)
        assert any("issued_at=" in m and "age=" in m for m in warning_messages)

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


def test_refresh_with_orphaned_user_returns_401_and_clears_cookie(
    auth_client: TestClient, pg_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A refresh-token row whose `user_id` no longer matches a live `users`
    row → 401 + cookie cleared.

    `users` has ON DELETE CASCADE on `refresh_tokens.user_id` so the orphan
    state shouldn't normally occur, but the production code defends against
    a race-window between issuing the token and the user row disappearing.
    Mirror that defence in tests so a regression that drops the cookie
    clear (or the 401) doesn't slip through.
    """
    import logging as _logging

    plaintext = "ghost-user-refresh-token"
    user_id = _seed_user_and_token(pg_url, plaintext=plaintext)

    # Drop the user row directly; the FK is configured ON DELETE CASCADE,
    # so deleting the user normally wipes their refresh tokens too. We
    # disable the constraint for this single test so the orphan state can
    # actually exist on the wire — that's the race the production code
    # guards against.
    engine = create_engine(pg_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE refresh_tokens DROP CONSTRAINT refresh_tokens_user_id_fkey")
            )
            conn.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
        try:
            with caplog.at_level(_logging.INFO, logger="app.routers.auth"):
                resp = auth_client.post(
                    "/api/auth/refresh",
                    cookies={"refresh_token": plaintext},
                )
        finally:
            # Clear the orphaned `refresh_tokens` row before re-adding the
            # FK; otherwise PG refuses to validate the constraint. Then
            # restore the FK so subsequent tests start from a clean schema.
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM refresh_tokens WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                conn.execute(
                    text(
                        "ALTER TABLE refresh_tokens "
                        "ADD CONSTRAINT refresh_tokens_user_id_fkey "
                        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
                    )
                )
    finally:
        engine.dispose()

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_refresh_token"
    # Cookie clear is load-bearing — the client must stop replaying.
    assert "refresh_token=" in resp.headers.get("set-cookie", "")
    # Differentiated INFO log so ops can grep "user_not_found".
    assert any("user_not_found" in record.getMessage() for record in caplog.records)


def test_refresh_when_auth_disabled_returns_404(auth_client: TestClient, pg_url: str) -> None:
    """RFC 0001 § Rollout plan step 1 mirror for the lifecycle routes. Pinned
    here so a future refactor that relocates the gate to per-router doesn't
    silently leave `/api/auth/refresh` returning 401 (which leaks subsystem
    presence) under flag-off.

    Captures and restores the prior `get_settings` override rather than
    clearing it, so this test composes with whatever the surrounding fixture
    set up (recommended pattern from the #34 item #2 follow-up).
    """
    plaintext = "ought-not-to-be-checked"
    _seed_user_and_token(pg_url, plaintext=plaintext)
    prior = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: make_test_settings(
        auth_enabled=False,
        database_url=pg_url,
        refresh_cookie_secure=False,
    )
    try:
        resp = auth_client.post(
            "/api/auth/refresh",
            cookies={"refresh_token": plaintext},
        )
    finally:
        if prior is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = prior
    assert resp.status_code == 404


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
    assert body["accessToken"] != expired_jwt
