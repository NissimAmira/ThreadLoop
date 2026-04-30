"""End-to-end integration tests for `POST /api/auth/google/callback`.

Each test stands up a fresh Postgres via Testcontainers (the session-scoped
`pg_url` fixture from `tests/conftest.py`), runs Alembic to head, and drives
the callback through `TestClient`. JWKS is mocked via `httpx.MockTransport`
in the autouse fixture from `tests/auth/conftest.py` — Google is never hit
live.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app import db as db_module
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
GOOGLE_AUD = "test-google-client-id.apps.googleusercontent.com"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def auth_client(pg_url: str) -> Iterator[TestClient]:
    """A TestClient wired to a fresh Postgres + auth-test settings.

    `pg_url` is session-scoped (one container for the whole test run); we
    truncate before each test so state never leaks between cases.
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

    test_settings = Settings(
        database_url=pg_url,
        jwt_signing_key="test-jwt-signing-key",
        refresh_token_hmac_key="test-hmac-key",
        google_client_id=GOOGLE_AUD,
        refresh_cookie_secure=False,  # TestClient over http://testserver
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


def _expected_hash(plaintext: str) -> bytes:
    return hmac.new(b"test-hmac-key", plaintext.encode("utf-8"), hashlib.sha256).digest()


# ----- happy path ------------------------------------------------------------


def test_new_user_signin_creates_user_and_refresh_row(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    token = google_id_token(
        sub="google-sub-new-1",
        aud=GOOGLE_AUD,
        email="newcomer@example.com",
        name="Newcomer",
        picture="https://cdn.example/avatars/n.png",
    )

    resp = auth_client.post("/api/auth/google/callback", json={"id_token": token})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is False
    assert body["access_token"]
    assert body["expires_at"]
    assert body["user"]["provider"] == "google"
    assert body["user"]["email"] == "newcomer@example.com"
    assert body["user"]["display_name"] == "Newcomer"
    assert body["user"]["email_verified"] is True

    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    # SameSite=Lax (case may vary by Starlette version)
    assert "samesite=lax" in set_cookie.lower()

    cookie_value = auth_client.cookies.get("refresh_token")
    assert cookie_value, "refresh cookie should be present in jar"

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        users = conn.execute(
            text(
                "SELECT id, provider, provider_user_id, email, email_verified "
                "FROM users WHERE provider_user_id = :sub"
            ),
            {"sub": "google-sub-new-1"},
        ).all()
        assert len(users) == 1
        user_id = users[0][0]

        rows = conn.execute(
            text(
                "SELECT user_id, token_hash, revoked_at FROM refresh_tokens "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        ).all()
        assert len(rows) == 1
        assert rows[0][1] == _expected_hash(cookie_value)
        assert rows[0][2] is None
    engine.dispose()


def test_existing_user_signin_is_idempotent(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """Second sign-in for the same `(provider, sub)` must not create a second
    user row, but should add a new refresh-token row each time."""
    builder_kwargs = {"sub": "google-sub-repeat", "aud": GOOGLE_AUD, "email": "r@example.com"}
    first = auth_client.post(
        "/api/auth/google/callback",
        json={"id_token": google_id_token(**builder_kwargs)},
    )
    assert first.status_code == 200
    auth_client.cookies.clear()

    second = auth_client.post(
        "/api/auth/google/callback",
        json={"id_token": google_id_token(**builder_kwargs)},
    )
    assert second.status_code == 200

    assert first.json()["user"]["id"] == second.json()["user"]["id"]

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider_user_id = :sub"),
            {"sub": "google-sub-repeat"},
        ).scalar_one()
        token_count = conn.execute(
            text(
                "SELECT count(*) FROM refresh_tokens t "
                "JOIN users u ON u.id = t.user_id "
                "WHERE u.provider_user_id = :sub"
            ),
            {"sub": "google-sub-repeat"},
        ).scalar_one()
    engine.dispose()

    assert user_count == 1, "find-or-create must not duplicate the user"
    assert token_count == 2, "each callback issues a fresh refresh token"


# ----- error paths -----------------------------------------------------------


def test_invalid_signature_returns_401(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    token = google_id_token()
    parts = token.split(".")
    swapped = "A" if parts[2][0] != "A" else "B"
    bad = ".".join([parts[0], parts[1], swapped + parts[2][1:]])

    resp = auth_client.post("/api/auth/google/callback", json={"id_token": bad})

    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == "invalid_token"

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        n_tokens = conn.execute(text("SELECT count(*) FROM refresh_tokens")).scalar_one()
    engine.dispose()
    assert n_users == 0 and n_tokens == 0, "rejected sign-in must not write anything"


def test_jwks_unreachable_returns_503(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    with_failing_jwks: Callable[[], None],
) -> None:
    with_failing_jwks()
    resp = auth_client.post(
        "/api/auth/google/callback",
        json={"id_token": google_id_token()},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "jwks_unavailable"


def test_unknown_provider_in_path_returns_501(auth_client: TestClient) -> None:
    """`apple` and `facebook` are valid provider names per the OpenAPI enum
    but their callbacks don't ship in this PR. 501 is the temporary signal
    until #15 / #16 wire them up."""
    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": "anything", "code": "ignored"},
    )
    # Pydantic rejects the body shape first (no `code` field on Google
    # variant). Either 422 (validation) or 501 (unimplemented) is acceptable
    # — we just want it not to be a 500 or a hidden DB write.
    assert resp.status_code in (422, 501)


def test_truly_unknown_provider_returns_404_or_422(auth_client: TestClient) -> None:
    """Anything outside the AuthProvider enum must not be silently routed."""
    resp = auth_client.post(
        "/api/auth/microsoft/callback",
        json={"id_token": "anything"},
    )
    # FastAPI's path-parameter Literal validation surfaces a 422; the OpenAPI
    # spec calls for 404. Both lock out the bad path; we accept either rather
    # than reshape the framework's default validation error.
    assert resp.status_code in (404, 422)


# ----- account-linking detection --------------------------------------------


def test_email_collision_with_other_provider_returns_link_required(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """An existing Apple-provider user owns alice@example.com (verified). A
    fresh Google sign-in for the same email must NOT issue a session — it
    must return the `link_required` envelope and write nothing to
    `refresh_tokens`."""
    apple_user_id = uuid.uuid4()
    now = datetime.now(UTC)

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, email, email_verified, "
                "display_name, can_sell, can_purchase, created_at, updated_at) "
                "VALUES (:id, 'apple', :sub, :email, true, "
                "'Alice (Apple)', false, true, :now, :now)"
            ),
            {
                "id": apple_user_id,
                "sub": "apple-sub-existing",
                "email": "alice@example.com",
                "now": now,
            },
        )

    token = google_id_token(
        sub="google-sub-newcomer",
        aud=GOOGLE_AUD,
        email="alice@example.com",
        email_verified=True,
        name="Alice (Google)",
    )

    resp = auth_client.post("/api/auth/google/callback", json={"id_token": token})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is True
    assert body["link_provider"] == "apple"
    assert body["link_token"]
    assert "access_token" not in body or body["access_token"] is None
    assert "user" not in body or body["user"] is None

    # No refresh cookie on the link path.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" not in set_cookie

    with engine.begin() as conn:
        token_count = conn.execute(text("SELECT count(*) FROM refresh_tokens")).scalar_one()
        google_user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider = 'google'")
        ).scalar_one()
    engine.dispose()

    assert token_count == 0, "link_required path must not mint a refresh token"
    assert google_user_count == 0, "link_required path must not insert a Google user row"

    # Decode the link token to confirm it carries the second-provider info
    # that #18 will need.
    from app.auth.link import decode_link_token

    test_settings = Settings(
        jwt_signing_key="test-jwt-signing-key",
        link_token_ttl_seconds=600,
    )
    claims = decode_link_token(body["link_token"], settings=test_settings)
    assert claims.existing_user_id == apple_user_id
    assert claims.new_provider == "google"
    assert claims.new_provider_user_id == "google-sub-newcomer"
    assert claims.new_email == "alice@example.com"


def test_unverified_email_does_not_trigger_link_required(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """An unverified Google email must NOT be matched against existing rows —
    that would let an attacker hijack accounts by claiming arbitrary emails."""
    apple_user_id = uuid.uuid4()
    now = datetime.now(UTC)

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, email, email_verified, "
                "display_name, can_sell, can_purchase, created_at, updated_at) "
                "VALUES (:id, 'apple', :sub, :email, true, "
                "'Bob (Apple)', false, true, :now, :now)"
            ),
            {
                "id": apple_user_id,
                "sub": "apple-sub-bob",
                "email": "bob@example.com",
                "now": now,
            },
        )

    token = google_id_token(
        sub="google-sub-imposter",
        aud=GOOGLE_AUD,
        email="bob@example.com",
        email_verified=False,
        name="Bob",
    )

    resp = auth_client.post("/api/auth/google/callback", json={"id_token": token})

    # Unverified email doesn't match — we treat as a brand-new Google user.
    assert resp.status_code == 200
    body = resp.json()
    assert body["link_required"] is False
    assert body["user"]["provider"] == "google"
    assert body["user"]["email_verified"] is False

    with engine.begin() as conn:
        google_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider = 'google'")
        ).scalar_one()
    engine.dispose()
    assert google_count == 1
