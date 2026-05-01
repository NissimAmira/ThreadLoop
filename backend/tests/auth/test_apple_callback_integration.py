"""End-to-end integration tests for `POST /api/auth/apple/callback`.

Stands up a fresh Postgres via Testcontainers, runs Alembic to head, and
drives the callback through `TestClient`. The Apple JWKS endpoint is mocked
via `httpx.MockTransport` (autouse-installed below) so Apple is never hit
live, and the Google JWKS autouse from `tests/auth/conftest.py` keeps the
shared verifier paths happy when other test modules run alongside.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.main import app

pytestmark = pytest.mark.integration

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
APPLE_AUD = "com.threadloop.test.service"
APPLE_TEAM = "TESTTEAM01"
APPLE_KID = "TESTKID0001"


# ----- Apple JWKS fixtures (mirrors the Google ones in conftest.py) ---------


@dataclass
class AppleJwksPair:
    private_jwk: JsonWebKey
    jwks: dict[str, Any]
    sign: Callable[[dict[str, Any]], str]


@pytest.fixture
def apple_p8_pem() -> str:
    """Stand-in for the developer's `.p8` key used to sign `client_secret`."""
    key = JsonWebKey.generate_key("EC", "P-256", is_private=True)
    pem: bytes = key.as_pem(is_private=True)
    return pem.decode("ascii")


@pytest.fixture
def apple_jwks_pair() -> AppleJwksPair:
    private = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    private_dict = private.as_dict(is_private=True)
    private_dict["kid"] = "apple-test-kid-1"
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


def _apple_jwks_transport(jwks: dict[str, Any], *, fail: bool = False) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) != APPLE_JWKS_URL:
            return httpx.Response(404, json={"error": "unexpected url"})
        if fail:
            raise httpx.ConnectError("simulated outage", request=request)
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
def with_failing_apple_jwks(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    def apply() -> None:
        cache = _JwksCache(transport=_apple_jwks_transport({"keys": []}, fail=True))
        monkeypatch.setattr(apple_module, "_default_cache", cache)

    return apply


@pytest.fixture
def apple_id_token(apple_jwks_pair: AppleJwksPair) -> Callable[..., str]:
    now = int(time.time())

    def build(
        *,
        sub: str = "apple-sub-int-1",
        aud: str = APPLE_AUD,
        iss: str = "https://appleid.apple.com",
        email: str | None = "user@example.com",
        email_verified: bool | str = True,
        is_private_email: bool | str | None = False,
        iat: int | None = None,
        exp: int | None = None,
        extra: dict[str, Any] | None = None,
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
        if extra:
            payload.update(extra)
        return apple_jwks_pair.sign(payload)

    return build


# ----- TestClient + Postgres + settings wiring -----------------------------


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


@pytest.fixture
def auth_client(pg_url: str, apple_p8_pem: str) -> Iterator[TestClient]:
    """A TestClient wired to a fresh Postgres + auth-test settings.

    Apple secrets are populated with test values so `Settings()` accepts
    `auth_enabled=True`. The PEM is generated per-test so the
    `client_secret` cache never sees stale state across runs.
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
        auth_enabled=True,
        database_url=pg_url,
        jwt_signing_key="test-jwt-signing-key",
        refresh_token_hmac_key="test-hmac-key",
        google_client_id="test-google-client-id.apps.googleusercontent.com",
        apple_client_id=APPLE_AUD,
        apple_team_id=APPLE_TEAM,
        apple_key_id=APPLE_KID,
        apple_private_key=apple_p8_pem,
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


def _expected_hash(plaintext: str) -> bytes:
    return hmac.new(b"test-hmac-key", plaintext.encode("utf-8"), hashlib.sha256).digest()


# ----- happy paths ----------------------------------------------------------


def test_new_user_signin_with_real_email_creates_user_and_refresh_row(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    token = apple_id_token(
        sub="apple-sub-real-1",
        email="newcomer@example.com",
        email_verified=True,
        is_private_email=False,
    )

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "ignored-by-this-pr", "name": "Newcomer"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is False
    assert body["access_token"]
    assert body["expires_at"]
    assert body["user"]["provider"] == "apple"
    assert body["user"]["email"] == "newcomer@example.com"
    assert body["user"]["display_name"] == "Newcomer"
    assert body["user"]["email_verified"] is True

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
            {"sub": "apple-sub-real-1"},
        ).all()
        assert len(users) == 1
        user_id = users[0][0]

        rows = conn.execute(
            text("SELECT user_id, token_hash, revoked_at FROM refresh_tokens WHERE user_id = :uid"),
            {"uid": user_id},
        ).all()
        assert len(rows) == 1
        assert rows[0][1] == _expected_hash(cookie_value)
        assert rows[0][2] is None
    engine.dispose()


def test_new_user_signin_with_relay_email_creates_account(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """Hide-My-Email relay addresses must not error and must create a valid
    account (the user's email is the relay address verbatim — that's what the
    user sees in their Apple settings; mail to it forwards)."""
    token = apple_id_token(
        sub="apple-sub-relay-1",
        email="abc123@privaterelay.appleid.com",
        email_verified=True,
        is_private_email=True,
    )

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x", "name": "Relay User"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is False
    assert body["user"]["provider"] == "apple"
    assert body["user"]["email"] == "abc123@privaterelay.appleid.com"
    assert body["user"]["display_name"] == "Relay User"


def test_subsequent_signin_reuses_existing_user(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """Apple omits `name` on subsequent sign-ins; the existing row's
    `display_name` must be preserved."""
    builder_kwargs = {"sub": "apple-sub-repeat", "email": "r@example.com"}

    first = auth_client.post(
        "/api/auth/apple/callback",
        json={
            "id_token": apple_id_token(**builder_kwargs),
            "code": "x",
            "name": "Original Name",
        },
    )
    assert first.status_code == 200
    first_user_id = first.json()["user"]["id"]
    first_display_name = first.json()["user"]["display_name"]
    assert first_display_name == "Original Name"
    auth_client.cookies.clear()

    # Second call: same user, no `name` (Apple drops it after the first auth).
    second = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": apple_id_token(**builder_kwargs), "code": "x"},
    )
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["user"]["id"] == first_user_id
    assert second_body["user"]["display_name"] == "Original Name", (
        "subsequent sign-in must NOT overwrite display_name from a missing-name token"
    )

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider_user_id = :sub"),
            {"sub": "apple-sub-repeat"},
        ).scalar_one()
        token_count = conn.execute(
            text(
                "SELECT count(*) FROM refresh_tokens t "
                "JOIN users u ON u.id = t.user_id "
                "WHERE u.provider_user_id = :sub"
            ),
            {"sub": "apple-sub-repeat"},
        ).scalar_one()
    engine.dispose()

    assert user_count == 1, "find-or-create must not duplicate the user"
    assert token_count == 2, "each callback issues a fresh refresh token"


def test_first_signin_without_name_falls_back_to_email(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
) -> None:
    """If the client doesn't pass `name` and no existing row exists, fall back
    to email then to the literal default — same pattern as Google."""
    token = apple_id_token(sub="apple-no-name-1", email="someone@example.com")
    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["display_name"] == "someone@example.com"


def test_first_signin_without_name_or_email_uses_default(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
) -> None:
    """Edge case: neither client-side `name` nor a token-side `email` (Apple
    can omit email on subsequent sign-ins, but a brand-new sub with no email
    is the worst case)."""
    token = apple_id_token(sub="apple-no-anything-1", email=None)
    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["display_name"] == "ThreadLoop user"


# ----- error paths ----------------------------------------------------------


def test_invalid_signature_returns_401(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    token = apple_id_token()
    parts = token.split(".")
    swapped = "A" if parts[2][0] != "A" else "B"
    bad = ".".join([parts[0], parts[1], swapped + parts[2][1:]])

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": bad, "code": "x"},
    )

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
    apple_id_token: Callable[..., str],
    with_failing_apple_jwks: Callable[[], None],
) -> None:
    with_failing_apple_jwks()
    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": apple_id_token(), "code": "x"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "jwks_unavailable"


def test_missing_required_field_returns_422(auth_client: TestClient) -> None:
    """Apple body schema requires both `id_token` and `code`."""
    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": "anything"},  # missing `code`
    )
    assert resp.status_code == 422


# ----- account-linking detection (Apple specifics) --------------------------


def test_email_collision_with_other_provider_returns_link_required(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """An existing Google-provider user owns alice@example.com (verified). A
    fresh Apple sign-in for the same email — NOT a relay — must NOT issue a
    session; it must return the `link_required` envelope."""
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

    token = apple_id_token(
        sub="apple-sub-newcomer",
        email="alice@example.com",
        email_verified=True,
        is_private_email=False,
    )

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x", "name": "Alice (Apple)"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is True
    assert body["link_provider"] == "google"
    assert body["link_token"]
    assert body.get("access_token") is None
    assert body.get("user") is None

    set_cookie = resp.headers.get("set-cookie", "")
    assert "refresh_token=" not in set_cookie

    with engine.begin() as conn:
        token_count = conn.execute(text("SELECT count(*) FROM refresh_tokens")).scalar_one()
        apple_user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider = 'apple'")
        ).scalar_one()
    engine.dispose()

    assert token_count == 0, "link_required path must not mint a refresh token"
    assert apple_user_count == 0, "link_required path must not insert an Apple user row"

    # Decode the link token to confirm it carries the second-provider info.
    from app.auth.link import decode_link_token

    test_settings = Settings(
        jwt_signing_key="test-jwt-signing-key",
        link_token_ttl_seconds=600,
    )
    claims = decode_link_token(body["link_token"], settings=test_settings)
    assert claims.existing_user_id == google_user_id
    assert claims.new_provider == "apple"
    assert claims.new_provider_user_id == "apple-sub-newcomer"
    assert claims.new_email == "alice@example.com"


def test_apple_relay_bypasses_link_required(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """The defining Apple-specific check: an incoming relay address must NOT
    trigger `link_required` even if a different-provider user with that exact
    relay address (or any address) exists. Matching on a relay would be
    spurious — relay addresses are per-app — and would let an attacker
    provoke the link flow against any account by using Hide-My-Email."""
    google_user_id = uuid.uuid4()
    now = datetime.now(UTC)

    relay = "abc123@privaterelay.appleid.com"

    engine = create_engine(pg_url, future=True)
    with engine.begin() as conn:
        # Plant a Google user with the SAME relay address — worst case for the
        # bypass check. (In production this would never happen organically,
        # but the bypass must hold regardless of the DB state.)
        conn.execute(
            text(
                "INSERT INTO users "
                "(id, provider, provider_user_id, email, email_verified, "
                "display_name, can_sell, can_purchase, created_at, updated_at) "
                "VALUES (:id, 'google', :sub, :email, true, "
                "'Bob', false, true, :now, :now)"
            ),
            {
                "id": google_user_id,
                "sub": "google-sub-bob",
                "email": relay,
                "now": now,
            },
        )

    token = apple_id_token(
        sub="apple-sub-relay-bypass",
        email=relay,
        email_verified=True,
        is_private_email=True,  # the bypass trigger
    )

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x", "name": "Bob (Apple)"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["link_required"] is False, "is_private_email=true must bypass collision check"
    assert body["user"]["provider"] == "apple"
    assert body["user"]["email"] == relay

    with engine.begin() as conn:
        apple_user_count = conn.execute(
            text("SELECT count(*) FROM users WHERE provider = 'apple'")
        ).scalar_one()
    engine.dispose()
    assert apple_user_count == 1, "relay sign-in must create a fresh Apple identity"


def test_unverified_email_does_not_trigger_link_required(
    auth_client: TestClient,
    apple_id_token: Callable[..., str],
    pg_url: str,
) -> None:
    """Same guarantee as the Google branch: unverified email must NOT match
    against existing rows — that would let an attacker hijack accounts by
    claiming arbitrary emails."""
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

    token = apple_id_token(
        sub="apple-sub-imposter",
        email="carol@example.com",
        email_verified=False,
        is_private_email=False,
    )

    resp = auth_client.post(
        "/api/auth/apple/callback",
        json={"id_token": token, "code": "x"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["link_required"] is False
    assert body["user"]["provider"] == "apple"
    assert body["user"]["email_verified"] is False
