"""Tests for `app.config.Settings` invariants — specifically that
`AUTH_ENABLED=true` plus an unset auth secret fails loudly at construction
rather than producing mysterious 401s at request time.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_auth_disabled_allows_empty_secrets() -> None:
    """The default scaffold posture: flag off, secrets unset. Must work
    so local `make dev` boots without forcing the dev to invent secrets
    they don't yet need."""
    settings = Settings(
        auth_enabled=False,
        google_client_id="",
        jwt_signing_key="",
        refresh_token_hmac_key="",
    )
    assert settings.auth_enabled is False


def test_auth_enabled_with_empty_google_client_id_rejected() -> None:
    """An unset `GOOGLE_CLIENT_ID` would silently make every Google sign-in
    look like 'your token is invalid' (401) — actual fault is server config.
    """
    with pytest.raises(ValidationError, match="GOOGLE_CLIENT_ID"):
        Settings(
            auth_enabled=True,
            google_client_id="",
            jwt_signing_key="key",
            refresh_token_hmac_key="key",
        )


def test_auth_enabled_with_empty_jwt_signing_key_rejected() -> None:
    with pytest.raises(ValidationError, match="JWT_SIGNING_KEY"):
        Settings(
            auth_enabled=True,
            google_client_id="cid",
            jwt_signing_key="",
            refresh_token_hmac_key="key",
        )


def test_auth_enabled_with_empty_refresh_hmac_key_rejected() -> None:
    with pytest.raises(ValidationError, match="REFRESH_TOKEN_HMAC_KEY"):
        Settings(
            auth_enabled=True,
            google_client_id="cid",
            jwt_signing_key="key",
            refresh_token_hmac_key="",
        )


def test_auth_enabled_lists_all_missing_secrets() -> None:
    """When several are missing, the error names every one of them so the
    operator fixes the env in one shot."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            auth_enabled=True,
            google_client_id="",
            jwt_signing_key="",
            refresh_token_hmac_key="",
            apple_client_id="",
            apple_team_id="",
            apple_key_id="",
            apple_private_key="",
            facebook_app_id="",
            facebook_app_secret="",
        )
    msg = str(exc_info.value)
    assert "GOOGLE_CLIENT_ID" in msg
    assert "JWT_SIGNING_KEY" in msg
    assert "REFRESH_TOKEN_HMAC_KEY" in msg
    assert "APPLE_CLIENT_ID" in msg
    assert "APPLE_TEAM_ID" in msg
    assert "APPLE_KEY_ID" in msg
    assert "APPLE_PRIVATE_KEY" in msg
    assert "FACEBOOK_APP_ID" in msg
    assert "FACEBOOK_APP_SECRET" in msg


@pytest.mark.parametrize(
    "missing_field, env_var",
    [
        ("apple_client_id", "APPLE_CLIENT_ID"),
        ("apple_team_id", "APPLE_TEAM_ID"),
        ("apple_key_id", "APPLE_KEY_ID"),
        ("apple_private_key", "APPLE_PRIVATE_KEY"),
    ],
)
def test_auth_enabled_with_missing_apple_secret_rejected(missing_field: str, env_var: str) -> None:
    """Same semantics as the Google guard: a missing Apple secret fails loudly
    at `Settings()` construction rather than at request time."""
    kwargs: dict[str, object] = dict(
        auth_enabled=True,
        google_client_id="cid",
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        apple_client_id="apple-cid",
        apple_team_id="TEAM000001",
        apple_key_id="KID0000001",
        apple_private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        facebook_app_id="fb-app-id",
        facebook_app_secret="fb-app-secret",
    )
    kwargs[missing_field] = ""
    with pytest.raises(ValidationError, match=env_var):
        Settings(**kwargs)


@pytest.mark.parametrize(
    "missing_field, env_var",
    [
        ("facebook_app_id", "FACEBOOK_APP_ID"),
        ("facebook_app_secret", "FACEBOOK_APP_SECRET"),
    ],
)
def test_auth_enabled_with_missing_facebook_secret_rejected(
    missing_field: str, env_var: str
) -> None:
    """Same loud-fail semantics for the Facebook callback's required app
    credentials: without the APP_ID we can't validate /debug_token responses,
    and without the APP_SECRET we can't construct the app access token at all.
    """
    kwargs: dict[str, object] = dict(
        auth_enabled=True,
        google_client_id="cid",
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        apple_client_id="apple-cid",
        apple_team_id="TEAM000001",
        apple_key_id="KID0000001",
        apple_private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        facebook_app_id="fb-app-id",
        facebook_app_secret="fb-app-secret",
    )
    kwargs[missing_field] = ""
    with pytest.raises(ValidationError, match=env_var):
        Settings(**kwargs)


def test_auth_enabled_with_all_secrets_set_constructs() -> None:
    settings = Settings(
        auth_enabled=True,
        google_client_id="cid",
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        apple_client_id="apple-cid",
        apple_team_id="TEAM000001",
        apple_key_id="KID0000001",
        apple_private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
        facebook_app_id="fb-app-id",
        facebook_app_secret="fb-app-secret",
    )
    assert settings.auth_enabled is True
