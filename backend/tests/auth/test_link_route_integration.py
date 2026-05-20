"""End-to-end integration tests for `POST /api/auth/link`.

The slice-4 BE half of Epic #11 (#18). The link route consumes a
`link_token` issued by a callback's `link_required` envelope and merges
the second-provider identity onto the existing user's `user_identities`
rows. Tests cover:

- happy path: Google original + Apple second → merged session
- happy path: Google original + Facebook second
- expiry: link token older than TTL → 401
- replay: same `jti` consumed twice → 401 on the second
- repeated linking: same identity linked twice (different jtis) → 200
  the second time but no duplicate `user_identities` row
- Apple-relay bypass honored end-to-end (relay sign-in never triggers
  link_required, so link route is never reachable for relay addresses)
- credential mismatch: re-auth as a different user → 401
- 409 conflict: second-provider identity already claimed by another user
- per-provider gating + AUTH_ENABLED=false 404 paths

The Google JWKS autouse fixture from `tests/auth/conftest.py` keeps the
Google-leg verifier's HTTP transport mocked. Apple JWKS uses the same
helper pattern as `test_apple_callback_integration.py`. Facebook is
verifier-stubbed in the route's namespace (the verifier itself is unit-
tested separately).
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from alembic import command
from app import db as db_module
from app.auth import apple as apple_module
from app.auth.apple import APPLE_JWKS_URL, _JwksCache
from app.auth.facebook import FacebookIdentity
from app.auth.link import issue_link_token
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app
from app.routers import auth as auth_router
from tests.auth._test_settings import make_test_settings

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"

GOOGLE_AUD = "test-google-client-id.apps.googleusercontent.com"
APPLE_AUD = "com.threadloop.test.service"
APPLE_TEAM = "TESTTEAM01"
APPLE_KID = "TESTKID0001"
FB_APP_ID = "test-facebook-app-id"
FB_APP_SECRET = "test-facebook-app-secret"


# ----- Apple JWKS fixtures (mirrors the Apple integration test's setup) -----


@dataclass
class AppleJwksPair:
    private_jwk: JsonWebKey
    jwks: dict[str, Any]
    sign: Callable[[dict[str, Any]], str]


@pytest.fixture
def apple_p8_pem() -> str:
    key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    pem: bytes = key.as_pem(is_private=True)
    return pem.decode("ascii")


@pytest.fixture
def apple_jwks_pair() -> AppleJwksPair:
    private = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    private_dict = private.as_dict(is_private=True)
    private_dict["kid"] = "apple-test-kid-link"
    private_dict["alg"] = "RS256"
    private_dict["use"] = "sig"

    public_dict = {
        k: v for k, v in private_dict.items() if k not in ("d", "p", "q", "dp", "dq", "qi")
    }
    public_dict["kid"] = private_dict["kid"]
    public_dict["alg"] = "RS256"
    public_dict["use"] = "sig"

    jwks = {"keys": [public_dict]}

    def sign(payload: dict[str, Any]) -> str:
        header = {"alg": "RS256", "kid": private_dict["kid"]}
        encoded = jwt.encode(header, payload, private_dict)
        return encoded.decode("ascii") if isinstance(encoded, bytes) else encoded

    return AppleJwksPair(
        private_jwk=JsonWebKey.import_key(private_dict),
        jwks=jwks,
        sign=sign,
    )


def _apple_jwks_transport(jwks: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) != APPLE_JWKS_URL:
            return httpx.Response(404, json={"error": "unexpected url"})
        return httpx.Response(200, content=json.dumps(jwks))

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _swap_apple_jwks_cache(
    apple_jwks_pair: AppleJwksPair, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    cache = _JwksCache(transport=_apple_jwks_transport(apple_jwks_pair.jwks))
    monkeypatch.setattr(apple_module, "_default_cache", cache)
    yield


@pytest.fixture
def apple_id_token(apple_jwks_pair: AppleJwksPair) -> Callable[..., str]:
    now = int(time.time())

    def build(
        *,
        sub: str = "apple-sub-link-1",
        aud: str = APPLE_AUD,
        iss: str = "https://appleid.apple.com",
        email: str | None = "user@example.com",
        email_verified: bool | str = True,
        is_private_email: bool | str | None = False,
        iat: int | None = None,
        exp: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "sub": sub,
            "aud": aud,
            "iss": iss,
            "iat": iat if iat is not None else now,
            "exp": exp if exp is not None else now + 3600,
        }
        if email is not None:
            payload["email"] = email
            payload["email_verified"] = email_verified
            if is_private_email is not None:
                payload["is_private_email"] = is_private_email
        return apple_jwks_pair.sign(payload)

    return build


# ----- TestClient + Postgres + settings wiring ------------------------------


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def auth_client(pg_url: str, apple_p8_pem: str) -> Iterator[TestClient]:
    cfg = _alembic_config(pg_url)
    command.upgrade(cfg, "head")

    engine = create_engine(pg_url, future=True)
    test_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Truncate the auth tables before each test. CASCADE on `users` covers
    # `refresh_tokens` and `user_identities` (both FK with cascade);
    # `consumed_link_tokens` has no FK so we name it explicitly.
    with engine.begin() as conn:
        for table in (
            "consumed_link_tokens",
            "refresh_tokens",
            "user_identities",
            "users",
        ):
            conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

    def override_get_db() -> Iterator[DbSession]:
        session = test_session_local()
        try:
            yield session
        finally:
            session.close()

    test_settings = make_test_settings(
        database_url=pg_url,
        google_client_id=GOOGLE_AUD,
        apple_client_id=APPLE_AUD,
        apple_team_id=APPLE_TEAM,
        apple_key_id=APPLE_KID,
        apple_private_key=apple_p8_pem,
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
    """Same shape as the Facebook integration test's stub fixture; we install
    one stub per test, called multiple times if the test exercises both the
    initial callback (collision detection) AND the link route (re-auth)."""

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
            assert app_id == FB_APP_ID
            assert app_secret == FB_APP_SECRET
            assert access_token
            if raises is not None:
                raise raises
            assert identity is not None
            return identity

        monkeypatch.setattr(auth_router, "verify_facebook_access_token", fake_verify)

    return install


def _link_settings_for(client: TestClient) -> Settings:
    """Read the settings the route is using out of dependency_overrides so
    test-side decode_link_token uses the same JWT signing key the route
    just used to issue."""
    factory = app.dependency_overrides.get(get_settings)
    assert factory is not None, "auth_client fixture must install settings override"
    return factory()


# ============================================================================
# Detection unit-style assertions (sanity check that the existing collision
# detection path is unchanged after #18's `find_user_by_identity` refactor)
# ============================================================================


def test_collision_detection_still_returns_link_required_after_refactor(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
) -> None:
    """The pre-#18 detection path lived in #14/#15/#16 and is verified by
    those callbacks' own integration tests. Re-asserting the contract here
    catches any regression in the `find_user_by_identity` refactor (#18
    swapped the lookup mechanism but must preserve behaviour).
    """
    # Sign in with Apple first → primary identity.
    apple_resp = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-collision-1", email="alice@example.com"),
            "code": "x",
            "name": "Alice (Apple)",
        },
    )
    assert apple_resp.status_code == 200
    auth_client.cookies.clear()

    # Google sign-in for the same verified email → link_required envelope.
    google_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-collision-1",
                aud=GOOGLE_AUD,
                email="alice@example.com",
                email_verified=True,
            )
        },
    )
    assert google_resp.status_code == 200
    body = google_resp.json()
    assert body["linkRequired"] is True
    assert body["linkProvider"] == "apple"
    assert body["linkToken"]


# ============================================================================
# Happy path — Google original / Apple second
# ============================================================================


def test_link_merge_happy_path_google_then_apple(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """End-to-end: sign up via Google, attempt Apple sign-in for same email,
    re-auth with Google through `POST /api/auth/link` → merged session, the
    second-provider identity lives in `user_identities`, refresh tokens for
    the original session are revoked, a fresh session is issued.
    """
    # Step 1: original Google sign-in.
    first = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-link-1", aud=GOOGLE_AUD, email="alice@example.com"
            )
        },
    )
    assert first.status_code == 200
    assert first.json()["linkRequired"] is False
    google_user_id = first.json()["user"]["id"]
    auth_client.cookies.clear()

    # Step 2: Apple sign-in for same verified email → link_required envelope.
    pending = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-link-2", email="alice@example.com"),
            "code": "x",
            "name": "Alice (Apple)",
        },
    )
    assert pending.status_code == 200
    assert pending.json()["linkRequired"] is True
    link_token = pending.json()["linkToken"]
    assert pending.json()["linkProvider"] == "google"

    # Step 3: client re-authenticates with the original (Google) provider
    # and posts the link request.
    link_resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": link_token,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-link-1",
                    aud=GOOGLE_AUD,
                    email="alice@example.com",
                ),
            },
        },
    )

    assert link_resp.status_code == 200, link_resp.text
    body = link_resp.json()
    assert body["linkRequired"] is False
    assert body["accessToken"]
    assert body["expiresAt"]
    assert body["user"]["id"] == google_user_id, "merged session targets the existing user"
    assert body["user"]["provider"] == "google", (
        "primary provider preserved across merge — User.provider stays singular"
    )

    # Refresh cookie set on the merged session.
    cookie_value = auth_client.cookies.get("refresh_token")
    assert cookie_value, "merged session must set a fresh refresh cookie"

    # The Apple identity now lives in `user_identities` against the Google user.
    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        identity_rows = conn.execute(
            text(
                "SELECT provider, provider_user_id FROM user_identities "
                "WHERE user_id = :uid ORDER BY linked_at"
            ),
            {"uid": google_user_id},
        ).all()
        # Two identities: the primary (Google, registered at signup) and the
        # newly-linked Apple identity.
        assert len(identity_rows) == 2
        providers = {row[0] for row in identity_rows}
        assert providers == {"google", "apple"}
        apple_row = next(r for r in identity_rows if r[0] == "apple")
        assert apple_row[1] == "apple-link-2"

        # The consumed_link_tokens row exists for this jti.
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
        assert n_consumed == 1

        # No new `users` row was created on the link path.
        n_users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        assert n_users == 1, "link merge must not insert a new users row"

        # Outstanding refresh tokens for the existing user are revoked
        # (the linking action is a high-trust state change).
        active_tokens = conn.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE user_id = :uid AND revoked_at IS NULL"),
            {"uid": google_user_id},
        ).scalar_one()
        assert active_tokens == 1, "exactly one active refresh token after merge — the new one"
    engine.dispose()


def test_link_merge_via_facebook_second_provider(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    stub_facebook_verifier: Callable[..., None],
    pg_url: str,
) -> None:
    """Confirms `POST /api/auth/link` consumes a Facebook-targeted link_token
    and doesn't accidentally hard-code "second provider must be Apple."

    The link_token is hand-issued here rather than obtained from a stubbed
    Facebook callback so this test stays focused on the merge logic — the
    Facebook callback's own emission of a Facebook-targeted link_token is
    covered by `test_facebook_callback_integration.py`
    (`test_email_collision_with_google_user_returns_link_required`).
    """
    # Original Google sign-in.
    first = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-fb-link-1",
                aud=GOOGLE_AUD,
                email="bob@example.com",
            )
        },
    )
    assert first.status_code == 200
    google_user_id = uuid.UUID(first.json()["user"]["id"])
    auth_client.cookies.clear()

    # Synthesise the same link_token the Facebook callback's collision branch
    # issues, so this test exercises the merge logic in isolation.
    settings = _link_settings_for(auth_client)
    link_token, _ = issue_link_token(
        existing_user_id=google_user_id,
        new_provider="facebook",
        new_provider_user_id="fb-link-1",
        new_email="bob@example.com",
        settings=settings,
    )

    link_resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": link_token,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-fb-link-1",
                    aud=GOOGLE_AUD,
                    email="bob@example.com",
                ),
            },
        },
    )

    assert link_resp.status_code == 200, link_resp.text
    assert link_resp.json()["user"]["id"] == str(google_user_id)
    assert link_resp.json()["user"]["provider"] == "google"

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        identity_rows = conn.execute(
            text("SELECT provider, provider_user_id FROM user_identities WHERE user_id = :uid"),
            {"uid": google_user_id},
        ).all()
        providers = {row[0] for row in identity_rows}
        assert providers == {"google", "facebook"}, (
            "Facebook identity merged into Google primary user"
        )
    engine.dispose()


# ============================================================================
# Expired token → 401
# ============================================================================


def test_expired_link_token_rejected(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """A link_token whose `exp` has passed must 401 — and must NOT touch
    `consumed_link_tokens` (we don't record expired tokens)."""
    # Original Google sign-in.
    first = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-expired-1",
                aud=GOOGLE_AUD,
                email="carol@example.com",
            )
        },
    )
    assert first.status_code == 200
    google_user_id = uuid.UUID(first.json()["user"]["id"])
    auth_client.cookies.clear()

    # Issue a link_token with a backdated `iat` so it's already expired.
    settings = _link_settings_for(auth_client)
    expired_link_token, _ = issue_link_token(
        existing_user_id=google_user_id,
        new_provider="apple",
        new_provider_user_id="apple-would-link",
        new_email="carol@example.com",
        settings=settings,
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": expired_link_token,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-expired-1",
                    aud=GOOGLE_AUD,
                    email="carol@example.com",
                ),
            },
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_link_token"

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
        n_identities = conn.execute(
            text("SELECT count(*) FROM user_identities WHERE provider = 'apple'")
        ).scalar_one()
    engine.dispose()
    assert n_consumed == 0, "expired token must not be recorded as consumed"
    assert n_identities == 0, "expired token must not link any identity"


# ============================================================================
# Replay → 401
# ============================================================================


def test_link_token_replay_rejected(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """Same `jti` consumed twice: the second call MUST 401 even though the
    link_token's signature and expiry are still valid. Without single-use
    enforcement, a leaked link_token would be replayable for the full TTL.
    """
    # Set up a fresh link_token via the standard collision path.
    auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-replay-1", aud=GOOGLE_AUD, email="dave@example.com"
            )
        },
    )
    auth_client.cookies.clear()
    pending = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-replay-1", email="dave@example.com"),
            "code": "x",
            "name": "Dave (Apple)",
        },
    )
    assert pending.status_code == 200
    link_token = pending.json()["linkToken"]

    payload: dict[str, Any] = {
        "linkToken": link_token,
        "originalProvider": "google",
        "credential": {
            "idToken": google_id_token(
                sub="google-replay-1", aud=GOOGLE_AUD, email="dave@example.com"
            ),
        },
    }

    first_consume = auth_client.post("/api/auth/link", json=payload)
    assert first_consume.status_code == 200, first_consume.text

    auth_client.cookies.clear()

    # Critical: same `jti` (link_token byte-equal) replayed → 401.
    replay = auth_client.post("/api/auth/link", json=payload)
    assert replay.status_code == 401, (
        f"replay must be rejected; got {replay.status_code}: {replay.text}"
    )
    assert replay.json()["detail"]["code"] == "invalid_link_token"

    # Replay must NOT mutate state: still exactly one identity link, still
    # exactly one consumed_link_tokens row, no new users / refresh rows.
    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
        n_apple_identities = conn.execute(
            text("SELECT count(*) FROM user_identities WHERE provider = 'apple'")
        ).scalar_one()
    engine.dispose()
    assert n_consumed == 1, "replay must not record a second consumed_link_tokens row"
    assert n_apple_identities == 1, "replay must not duplicate the linked identity"


# ============================================================================
# Repeated linking attempt — same identity, fresh jti (the user clicks twice)
# ============================================================================


def test_repeated_link_with_fresh_jti_idempotent(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """The "repeated linking attempt" AC: two distinct link_tokens (each
    with its own `jti`, so neither is a replay) for the same target
    `(existing_user, second_provider, second_sub)` are consumed. Both
    succeed (200), but the second MUST NOT duplicate the
    `(provider, provider_user_id)` row in `user_identities`.

    Why we synthesise the link_tokens directly rather than driving the
    full callback path twice: after the first merge, the Apple identity
    IS in `user_identities` against the Google user, so the second
    `apple/callback` invocation no longer detects a collision — it just
    signs the user in normally and returns a regular session, never
    `link_required`. So the "user accidentally repeats the flow" scenario
    only reaches the link route a second time if they somehow held onto a
    second pre-issued link_token; the synthetic-link-token approach
    exercises that exact path.

    The route's idempotency comes from the pre-link `find_user_by_identity`
    check: if the second-provider identity already belongs to the same
    user, skip the insert (don't blow up on the unique constraint).
    """
    google_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-repeat-1",
                aud=GOOGLE_AUD,
                email="elena@example.com",
            )
        },
    )
    google_user_id = uuid.UUID(google_resp.json()["user"]["id"])
    auth_client.cookies.clear()

    settings = _link_settings_for(auth_client)

    def fresh_link_token() -> str:
        token, _ = issue_link_token(
            existing_user_id=google_user_id,
            new_provider="apple",
            new_provider_user_id="apple-repeat-1",
            new_email="elena@example.com",
            settings=settings,
        )
        return token

    payload_template: dict[str, Any] = {
        "originalProvider": "google",
        "credential": {
            "idToken": google_id_token(
                sub="google-repeat-1",
                aud=GOOGLE_AUD,
                email="elena@example.com",
            ),
        },
    }

    first = auth_client.post(
        "/api/auth/link",
        json={**payload_template, "linkToken": fresh_link_token()},
    )
    assert first.status_code == 200, first.text
    auth_client.cookies.clear()

    # Second link attempt with a fresh jti — Apple identity is already
    # linked to this user from the first call. Route MUST detect the
    # pre-existing-same-user case and not crash on the unique constraint.
    second = auth_client.post(
        "/api/auth/link",
        json={**payload_template, "linkToken": fresh_link_token()},
    )
    assert second.status_code == 200, second.text

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_apple_identities = conn.execute(
            text(
                "SELECT count(*) FROM user_identities "
                "WHERE provider = 'apple' AND provider_user_id = 'apple-repeat-1'"
            )
        ).scalar_one()
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
    engine.dispose()
    assert n_apple_identities == 1, (
        "repeated linking with same (provider, sub) must NOT duplicate identity row"
    )
    assert n_consumed == 2, "each fresh-jti consumption records a row"


# ============================================================================
# Apple-relay bypass honored end-to-end
# ============================================================================


def test_apple_relay_never_reaches_link_route(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """The Apple `is_private_email` bypass is shipped in #15 and ships a
    `linkRequired: false` envelope for relay sign-ins regardless of whether
    a same-email user exists. The slice-4 AC requires verifying this is
    honored end-to-end: a relay sign-in must NEVER produce a link_token
    that the link route could consume.

    Test shape: plant a Google user with the relay address (worst case for
    the bypass — same email as the relay), then sign in with Apple using
    `is_private_email=true`. Confirm the BE returns linkRequired=false +
    a fresh Apple session, NOT a link_required envelope. Implies the link
    route is unreachable on this path.
    """
    relay = "abc123@privaterelay.appleid.com"

    # Plant a Google user with the SAME relay address as the email — worst
    # case for the bypass check. (See `test_apple_relay_bypasses_link_required`
    # in test_apple_callback_integration.py for the same shape.)
    google_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-relay-1",
                aud=GOOGLE_AUD,
                email=relay,
                email_verified=True,
            )
        },
    )
    assert google_resp.status_code == 200
    auth_client.cookies.clear()

    apple_resp = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(
                sub="apple-relay-bypass-link",
                email=relay,
                email_verified=True,
                is_private_email=True,  # the bypass trigger
            ),
            "code": "x",
            "name": "Bob (Apple)",
        },
    )
    assert apple_resp.status_code == 200
    body = apple_resp.json()
    assert body["linkRequired"] is False, (
        "Apple relay must not trigger link_required end-to-end — "
        "no link_token to feed to POST /api/auth/link"
    )
    assert "linkToken" not in body or body["linkToken"] is None
    assert body["user"]["provider"] == "apple"

    # Two independent users: the planted Google + the new Apple. No link
    # row inserted by the relay-bypass path (the link route was never
    # called).
    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
    engine.dispose()
    assert n_users == 2, "relay path keeps the accounts independent"
    assert n_consumed == 0


# ============================================================================
# Original-provider mismatch (re-auth as wrong provider) → 401
# ============================================================================


def test_original_provider_mismatch_rejected(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
) -> None:
    """User claims `originalProvider=apple` but the link_token's existing
    user is a Google account. Must 401."""
    auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-mismatch-1",
                aud=GOOGLE_AUD,
                email="frank@example.com",
            )
        },
    )
    auth_client.cookies.clear()
    pending = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-mismatch-1", email="frank@example.com"),
            "code": "x",
            "name": "Frank (Apple)",
        },
    )
    link_token = pending.json()["linkToken"]

    # Wrong original_provider: claim Apple even though existing user is Google.
    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": link_token,
            "originalProvider": "apple",
            "credential": {
                "idToken": apple_id_token(sub="apple-something", email="frank@example.com"),
                "code": "x",
            },
        },
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_link_token"


# ============================================================================
# Credential resolves to a different user → 401
# ============================================================================


def test_credential_for_wrong_user_rejected(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
) -> None:
    """User holds the link_token for Alice but submits Bob's Google
    credential. Must 401 — exactly the attack the link flow guards against.
    """
    # Alice is a Google user; collision with Apple kicks the link_required.
    auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-alice-1", aud=GOOGLE_AUD, email="alice@example.com"
            )
        },
    )
    auth_client.cookies.clear()

    # Bob is a separate Google user (no collision).
    auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(sub="google-bob-1", aud=GOOGLE_AUD, email="bob@example.com")
        },
    )
    auth_client.cookies.clear()

    # Apple sign-in for alice@example.com → link_required for Alice's
    # Google account.
    pending = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-attack-1", email="alice@example.com"),
            "code": "x",
            "name": "Attacker",
        },
    )
    link_token = pending.json()["linkToken"]

    # Attacker (knows Alice's link_token somehow) re-auths as themselves
    # (Bob) — the credential's `sub` does NOT equal Alice's primary
    # provider_user_id.
    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": link_token,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-bob-1",
                    aud=GOOGLE_AUD,
                    email="bob@example.com",
                ),
            },
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_link_token"


# ============================================================================
# Tampered link_token → 401
# ============================================================================


def test_tampered_link_token_rejected(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
) -> None:
    """A link_token whose signature has been mutated must 401 with the
    standard envelope — not 500, not 422."""
    # Need a real link_token to mutate; produce one via the Google->Apple
    # collision path used elsewhere.
    google_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-tamper-1",
                aud=GOOGLE_AUD,
                email="grace@example.com",
            )
        },
    )
    google_user_id = uuid.UUID(google_resp.json()["user"]["id"])
    auth_client.cookies.clear()

    # Hand-issue a token, then flip a byte in the signature.
    settings = _link_settings_for(auth_client)
    legit, _ = issue_link_token(
        existing_user_id=google_user_id,
        new_provider="apple",
        new_provider_user_id="apple-would-link",
        new_email="grace@example.com",
        settings=settings,
    )
    parts = legit.split(".")
    swapped = "A" if parts[2][0] != "A" else "B"
    tampered = ".".join([parts[0], parts[1], swapped + parts[2][1:]])

    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": tampered,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-tamper-1",
                    aud=GOOGLE_AUD,
                    email="grace@example.com",
                ),
            },
        },
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_link_token"


# ============================================================================
# Identity already claimed by another user → 409
# ============================================================================


def test_second_provider_identity_already_claimed_returns_409(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """If a `user_identities` row for `(claims.new_provider,
    claims.new_provider_user_id)` already exists belonging to a DIFFERENT
    user (race / concurrent linking / data anomaly), the link cannot
    proceed. 409, not 401, so the FE can distinguish the genuine conflict
    from the attack-signal cases.
    """
    # User A: Google primary.
    a_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(sub="google-a-1", aud=GOOGLE_AUD, email="hannah@example.com")
        },
    )
    user_a_id = uuid.UUID(a_resp.json()["user"]["id"])
    auth_client.cookies.clear()

    # User B: Apple primary, signed in independently (separate email so no
    # collision detection fires).
    auth_client.post(
        "/api/auth/apple/callback",
        json={
            "idToken": apple_id_token(sub="apple-shared-1", email="ivan@example.com"),
            "code": "x",
            "name": "Ivan (Apple)",
        },
    )
    auth_client.cookies.clear()

    # Hand-issue a link_token claiming `(apple, apple-shared-1)` should be
    # linked onto user A — but B already owns that Apple identity.
    settings = _link_settings_for(auth_client)
    bad_link, _ = issue_link_token(
        existing_user_id=user_a_id,
        new_provider="apple",
        new_provider_user_id="apple-shared-1",  # already User B's
        new_email="hannah@example.com",
        settings=settings,
    )

    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": bad_link,
            "originalProvider": "google",
            "credential": {
                "idToken": google_id_token(
                    sub="google-a-1",
                    aud=GOOGLE_AUD,
                    email="hannah@example.com",
                ),
            },
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["code"] == "identity_already_linked"

    # No state mutation — the consumed_link_tokens row was NOT inserted
    # (we 409 before recording consumption, since the link didn't actually
    # happen).
    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        n_consumed = conn.execute(text("SELECT count(*) FROM consumed_link_tokens")).scalar_one()
    engine.dispose()
    assert n_consumed == 0


# ============================================================================
# AUTH_ENABLED=false → 404
# ============================================================================


def test_link_route_returns_404_when_auth_disabled(
    auth_client: TestClient,
) -> None:
    """RFC 0001 § Rollout plan: every `/api/auth/*` route returns 404 when
    `AUTH_ENABLED=false`. The link route is no exception.
    """
    test_settings = make_test_settings(
        auth_enabled=False,
        database_url="postgresql+psycopg://x:x@nope/x",
        google_client_id=GOOGLE_AUD,
        refresh_cookie_secure=False,
    )
    prev_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: test_settings
    try:
        resp = auth_client.post(
            "/api/auth/link",
            json={
                "linkToken": "anything",
                "originalProvider": "google",
                "credential": {"idToken": "anything"},
            },
        )
    finally:
        if prev_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = prev_override
    assert resp.status_code == 404


# ============================================================================
# Body validation — missing field → 422
# ============================================================================


def test_link_route_missing_link_token_returns_422(auth_client: TestClient) -> None:
    """Pydantic validation rejects missing required fields with 422."""
    resp = auth_client.post(
        "/api/auth/link",
        json={
            "originalProvider": "google",
            "credential": {"idToken": "x"},
        },
    )
    assert resp.status_code == 422


def test_link_route_invalid_credential_shape_returns_422(
    auth_client: TestClient,
    google_id_token: Callable[..., str],
) -> None:
    """A credential body that doesn't satisfy the Google schema (missing
    `idToken`) must 422 — same posture as the original
    `POST /api/auth/google/callback`.
    """
    google_resp = auth_client.post(
        "/api/auth/google/callback",
        json={
            "idToken": google_id_token(
                sub="google-shape-1",
                aud=GOOGLE_AUD,
                email="kate@example.com",
            )
        },
    )
    google_user_id = uuid.UUID(google_resp.json()["user"]["id"])
    auth_client.cookies.clear()

    settings = _link_settings_for(auth_client)
    link_token, _ = issue_link_token(
        existing_user_id=google_user_id,
        new_provider="apple",
        new_provider_user_id="apple-would-link",
        new_email="kate@example.com",
        settings=settings,
    )

    resp = auth_client.post(
        "/api/auth/link",
        json={
            "linkToken": link_token,
            "originalProvider": "google",
            "credential": {"foo": "bar"},  # missing idToken
        },
    )
    assert resp.status_code == 422
