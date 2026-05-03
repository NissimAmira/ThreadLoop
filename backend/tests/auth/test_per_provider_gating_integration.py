"""End-to-end coverage for per-provider auth gating (issue #51).

The unit-level config tests in `tests/test_config.py` prove `Settings()`
constructs when only one provider is enabled. This file proves the same
configuration is *operationally* sound: with only `GOOGLE_ENABLED=true`,
the Google callback works while Apple and Facebook return 404 — without
needing dummy Apple/Facebook secrets in the environment.

This is the regression Epic #11's slice-1 smoke session caught: the old
all-or-nothing validator forced operators to stuff Apple/FB dummy values
into `.env` to boot a Google-only demo, at which point the validator no
longer protected against the misconfiguration it was designed to catch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
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
def google_only_auth_client(pg_url: str) -> Iterator[TestClient]:
    """A TestClient wired to Settings with ONLY Google enabled — no Apple
    or Facebook secrets supplied.

    The point of this fixture is to construct `Settings(...)` without any
    of the Apple/Facebook fields touched, modelling an operator who runs
    `make dev` with just `AUTH_ENABLED=true`, `GOOGLE_ENABLED=true`, and
    the cross-cutting + Google secrets in their `.env`. We deliberately
    do not use `make_test_settings` here because that factory pre-fills
    every secret — defeating the point of the test.
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

    google_only_settings = Settings(
        auth_enabled=True,
        google_enabled=True,
        apple_enabled=False,
        facebook_enabled=False,
        jwt_signing_key="test-jwt-signing-key",
        refresh_token_hmac_key="test-hmac-key",
        google_client_id=GOOGLE_AUD,
        # Critically: no apple_*, no facebook_*. Defaults to "".
        database_url=pg_url,
        refresh_cookie_secure=False,
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: google_only_settings
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


def test_google_only_settings_constructs_without_other_provider_secrets() -> None:
    """AC item: `Settings()` constructs without error when only Google is
    enabled, with no Apple / Facebook values supplied at all."""
    settings = Settings(
        auth_enabled=True,
        google_enabled=True,
        apple_enabled=False,
        facebook_enabled=False,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        google_client_id="cid",
    )
    assert settings.auth_enabled is True
    assert settings.google_enabled is True
    assert settings.apple_enabled is False
    assert settings.facebook_enabled is False
    # Empty defaults preserved on the disabled providers.
    assert settings.apple_client_id == ""
    assert settings.apple_team_id == ""
    assert settings.apple_key_id == ""
    assert settings.apple_private_key == ""
    assert settings.facebook_app_id == ""
    assert settings.facebook_app_secret == ""


def test_google_only_callback_works(
    google_only_auth_client: TestClient,
    google_id_token: Callable[..., str],
) -> None:
    """AC item: with only Google enabled, the Google callback succeeds end-
    to-end. The autouse JWKS mock from `conftest.py` lets the verifier
    accept the synthetic ID token without hitting Google live."""
    token = google_id_token(
        sub="google-only-sub-1",
        aud=GOOGLE_AUD,
        email="solo@example.com",
        name="Solo Google",
    )
    resp = google_only_auth_client.post(
        "/api/auth/google/callback",
        json={"idToken": token},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["linkRequired"] is False
    assert body["user"]["provider"] == "google"
    assert body["user"]["email"] == "solo@example.com"


def test_google_only_apple_callback_returns_404(
    google_only_auth_client: TestClient,
) -> None:
    """AC item: with Apple disabled, its callback 404s — even if the body
    is well-formed."""
    resp = google_only_auth_client.post(
        "/api/auth/apple/callback",
        json={"idToken": "any", "code": "any"},
    )
    assert resp.status_code == 404


def test_google_only_facebook_callback_returns_404(
    google_only_auth_client: TestClient,
) -> None:
    """AC item: with Facebook disabled, its callback 404s — even if the
    body is well-formed."""
    resp = google_only_auth_client.post(
        "/api/auth/facebook/callback",
        json={"accessToken": "EAA-anything"},
    )
    assert resp.status_code == 404


def test_google_only_disabled_provider_404_wins_over_body_validation(
    google_only_auth_client: TestClient,
) -> None:
    """The per-provider gate runs BEFORE body validation. A malformed body
    on a disabled provider must 404, not 422 — otherwise an unauthenticated
    probe could distinguish "this provider is disabled in this deployment"
    from "this provider is enabled but you sent garbage", which is the
    same surface the master 404 path is designed to hide."""
    resp_apple = google_only_auth_client.post(
        "/api/auth/apple/callback",
        json={"completely": "wrong"},
    )
    assert resp_apple.status_code == 404

    resp_facebook = google_only_auth_client.post(
        "/api/auth/facebook/callback",
        json={},
    )
    assert resp_facebook.status_code == 404
