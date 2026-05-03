"""End-to-end integration tests for `POST /api/auth/facebook/callback`.

Each test stands up a fresh Postgres via Testcontainers (the session-scoped
`pg_url` fixture from `tests/conftest.py`), runs Alembic to head, and drives
the callback through `TestClient`.

Unlike the Google and Apple integration tests — which mock the JWKS HTTP
fetch via `httpx.MockTransport` and let the real verifier do the JWT
crypto — the Facebook flow has no JWT to verify. We monkeypatch the
verifier itself in the router's namespace so each test can assert the route
layer's behaviour (find-or-create, collision detection, error mapping)
without needing to construct realistic Graph API payloads at every test.
The verifier's own behaviour is covered by `test_facebook_verifier.py`.
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
from app.auth.facebook import (
    FacebookIdentity,
    GraphApiUnavailableError,
    InvalidFacebookTokenError,
)
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.routers import auth as auth_router
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
FB_APP_ID = "test-facebook-app-id"
FB_APP_SECRET = "test-facebook-app-secret"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def auth_client(pg_url: str) -> Iterator[TestClient]:
    """A TestClient wired to a fresh Postgres + auth-test settings.

    `pg_url` is session-scoped; we truncate before each test so state never
    leaks between cases.
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
        facebook_app_id=FB_APP_ID,
        facebook_app_secret=FB_APP_SECRET,
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


@pytest.fixture
def stub_facebook_verifier(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Monkeypatch the verifier as imported into `app.routers.auth`.

    Returns a setter that takes either a `FacebookIdentity` (happy path) or
    an exception class (raising path) and installs the corresponding stub.
    """

    def install(
        *,
        identity: FacebookIdentity | None = None,
        raises: Exception | None = None,
    ) -> None:
        def fake_verify(
            access_token: str,
            *,
            app_id: str,
            app_secret: str,
        ) -> FacebookIdentity:
            # Sanity-check the route is wiring through Settings correctly.
            assert app_id == FB_APP_ID, f"unexpected app_id: {app_id!r}"
            assert app_secret == FB_APP_SECRET, f"unexpected app_secret: {app_secret!r}"
            assert access_token, "verifier must receive a non-empty token"
            if raises is not None:
                raise raises
            assert identity is not None, "test must set identity or raises"
            return identity

        monkeypatch.setattr(auth_router, "verify_facebook_access_token", fake_verify)

    return install


def _expected_hash(plaintext: str) -> bytes:
    return hmac.new(b"test-hmac-key", plaintext.encode("utf-8"), hashlib.sha256).digest()


# ----- happy paths ----------------------------------------------------------


def test_new_user_signin_with_email_creates_user_and_refresh_row(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-new-1",
            email="newcomer@example.com",
            email_verified=False,  # Facebook never sets this True per design
            name="Newcomer",
            picture="https://cdn.fb/avatar.png",
        )
    )

    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "EAA-fake-user-token"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["linkRequired"] is False
    assert body["accessToken"]
    assert body["expiresAt"]
    assert body["user"]["provider"] == "facebook"
    assert body["user"]["email"] == "newcomer@example.com"
    assert body["user"]["displayName"] == "Newcomer"
    assert body["user"]["emailVerified"] is False
    assert body["user"]["avatarUrl"] == "https://cdn.fb/avatar.png"

    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
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
            {"sub": "fb-sub-new-1"},
        ).all()
        assert len(users) == 1
        assert users[0][1] == "facebook"
        user_id = users[0][0]

        rows = conn.execute(
            text("SELECT user_id, token_hash, revoked_at FROM refresh_tokens WHERE user_id = :uid"),
            {"uid": user_id},
        ).all()
        assert len(rows) == 1
        assert rows[0][1] == _expected_hash(cookie_value)
        assert rows[0][2] is None
    engine.dispose()


def test_new_user_signin_without_email_creates_user(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
) -> None:
    """User declined the `email` permission — /me omits the field. Account
    must still be createable, with display_name falling back to `name`."""
    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-no-email",
            email=None,
            email_verified=False,
            name="No Email Person",
            picture=None,
        )
    )
    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "EAA-no-email"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["linkRequired"] is False
    # `response_model_exclude_none=True` strips null fields rather than
    # serialising them as JSON `null` — match Google / Apple's behaviour.
    assert body["user"].get("email") is None
    assert body["user"]["displayName"] == "No Email Person"


def test_new_user_signin_without_name_or_email_uses_default(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
) -> None:
    """Worst case: /me gives us only the `id`. Display name falls back to the
    literal default — same chain as Google / Apple."""
    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-bare",
            email=None,
            email_verified=False,
            name=None,
            picture=None,
        )
    )
    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "EAA-bare"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["displayName"] == "ThreadLoop user"


def test_subsequent_signin_reuses_existing_user_without_overwrite(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    """Find-or-create on (provider='facebook', provider_user_id=id). The
    second sign-in must NOT overwrite the existing row's display_name,
    matching the Google / Apple behaviour even when /me carries a different
    name (e.g. user changed it on Facebook between sessions)."""
    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-repeat",
            email="r@example.com",
            email_verified=False,
            name="Original Name",
            picture=None,
        )
    )
    first = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "first-call"},
    )
    assert first.status_code == 200
    first_user_id = first.json()["user"]["id"]
    assert first.json()["user"]["displayName"] == "Original Name"
    auth_client.cookies.clear()

    # Second call: same sub, but the verifier surfaces a different name.
    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-repeat",
            email="r@example.com",
            email_verified=False,
            name="New Name From FB",
            picture=None,
        )
    )
    second = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "second-call"},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["user"]["id"] == first_user_id
    assert second_body["user"]["displayName"] == "Original Name", (
        "subsequent sign-in must NOT overwrite display_name"
    )

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider_user_id = :sub"),
            {"sub": "fb-sub-repeat"},
        ).scalar_one()
        token_count = conn.execute(
            text(
                "SELECT count(*) FROM refresh_tokens t "
                "JOIN users u ON u.id = t.user_id "
                "WHERE u.provider_user_id = :sub"
            ),
            {"sub": "fb-sub-repeat"},
        ).scalar_one()
    engine.dispose()

    assert user_count == 1, "find-or-create must not duplicate the user"
    assert token_count == 2, "each callback issues a fresh refresh token"


# ----- error paths ----------------------------------------------------------


def test_invalid_token_returns_401(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    stub_facebook_verifier(raises=InvalidFacebookTokenError("token rejected by /me"))

    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "bad"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["detail"]["code"] == "invalid_token"
    # Ensure the route doesn't echo the verifier message — that path can leak
    # token contents per the docstring on _handle_facebook_callback.
    assert "rejected by /me" not in body["detail"]["message"]

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        n_tokens = conn.execute(text("SELECT count(*) FROM refresh_tokens")).scalar_one()
    engine.dispose()
    assert n_users == 0 and n_tokens == 0, "rejected sign-in must not write anything"


def test_graph_api_unavailable_returns_503(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
) -> None:
    stub_facebook_verifier(raises=GraphApiUnavailableError("Graph 502"))
    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "anything"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "graph_api_unavailable"


def test_missing_access_token_field_returns_422(auth_client: TestClient) -> None:
    """Body schema requires `access_token`."""
    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={},
    )
    assert resp.status_code == 422


def test_empty_access_token_returns_422(auth_client: TestClient) -> None:
    """Pydantic min_length=1 enforces non-empty at the body schema layer."""
    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": ""},
    )
    assert resp.status_code == 422


def test_facebook_disabled_returns_404(auth_client: TestClient) -> None:
    """Per-provider gating (#51): with `AUTH_ENABLED=true` but
    `FACEBOOK_ENABLED=false`, the Facebook callback returns 404. The 404
    wins over the body validator — a malformed body for a disabled
    provider must not produce a 422 that leaks the contract surface."""
    test_settings = make_test_settings(
        database_url="postgresql+psycopg://x:x@nope/x",
        facebook_app_id=FB_APP_ID,
        facebook_app_secret=FB_APP_SECRET,
        facebook_enabled=False,
        refresh_cookie_secure=False,
    )
    # Capture the prior override (set by the `auth_client` fixture) so the
    # `finally` restores the *exact* settings object the fixture installed,
    # rather than building a fresh `make_test_settings(...)` that drifts as
    # the factory's defaults change.
    prev_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    try:
        resp = auth_client.post(
            "/api/auth/facebook/callback",
            json={"accessToken": "EAA-fake"},
        )
        # Even a malformed body must 404, not 422 — the per-provider gate
        # runs before body validation.
        bad_body_resp = auth_client.post(
            "/api/auth/facebook/callback",
            json={},
        )
    finally:
        if prev_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = prev_override
    assert resp.status_code == 404
    assert bad_body_resp.status_code == 404


# ----- account-linking detection (Facebook specifics) -----------------------


def test_email_collision_does_not_trigger_link_required_facebook(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    """The Facebook-specific guarantee: because Graph API doesn't expose
    `email_verified`, the verifier always sets it False, so the cross-provider
    collision check never fires for Facebook sign-ins. A Facebook user whose
    email matches an existing verified Google user must NOT trip the
    `link_required` envelope — they get a fresh independent Facebook
    identity, and account merging happens (if at all) through the user-
    initiated linking flow shipping in #18.
    """
    google_user_id = uuid.uuid4()
    now = datetime.now(UTC)

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, email, email_verified, "
                "display_name, can_sell, can_purchase, created_at, updated_at) "
                "VALUES (:id, 'google', :sub, :email, true, "
                "'Alice (Google)', false, true, :now, :now)"
            ),
            {
                "id": google_user_id,
                "sub": "google-sub-existing",
                "email": "alice@example.com",
                "now": now,
            },
        )

    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-newcomer",
            email="alice@example.com",
            email_verified=False,  # Facebook never claims verified
            name="Alice (Facebook)",
            picture=None,
        )
    )

    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "anything"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["linkRequired"] is False, (
        "Facebook email_verified is always False so collision check must NOT fire"
    )
    assert body["user"]["provider"] == "facebook"
    assert body["user"]["email"] == "alice@example.com"

    with engine.begin() as conn:
        fb_user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider = 'facebook'")
        ).scalar_one()
    engine.dispose()
    assert fb_user_count == 1, "Facebook sign-in must create a fresh independent user"


def test_unverified_email_does_not_trigger_link_required(
    auth_client: TestClient,
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    """Same guarantee as the Google / Apple branches: unverified email must NOT
    match against existing rows. For Facebook this is the dominant case (the
    verifier hard-codes `email_verified=False`), but assert it explicitly so a
    future change to the verifier — e.g. exposing a verified flag — still
    passes through this guard rather than silently enabling auto-merge.
    """
    google_user_id = uuid.uuid4()
    now = datetime.now(UTC)

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, email, email_verified, "
                "display_name, can_sell, can_purchase, created_at, updated_at) "
                "VALUES (:id, 'google', :sub, :email, true, "
                "'Carol (Google)', false, true, :now, :now)"
            ),
            {
                "id": google_user_id,
                "sub": "google-sub-carol",
                "email": "carol@example.com",
                "now": now,
            },
        )

    stub_facebook_verifier(
        identity=FacebookIdentity(
            sub="fb-sub-imposter",
            email="carol@example.com",
            email_verified=False,
            name=None,
            picture=None,
        )
    )

    resp = auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "anything"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["linkRequired"] is False
    assert body["user"]["provider"] == "facebook"
    assert body["user"]["emailVerified"] is False
    engine.dispose()
