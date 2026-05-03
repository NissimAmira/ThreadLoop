"""Tests for `app.config.Settings` invariants — specifically that
`AUTH_ENABLED=true` plus an unset auth secret for an enabled provider fails
loudly at construction rather than producing mysterious 401s at request time.

Per-provider gating (issue #51) means the validator now checks each
provider's secrets only when its `<PROVIDER>_ENABLED` flag is True. The
cross-cutting `JWT_SIGNING_KEY` and `REFRESH_TOKEN_HMAC_KEY` are still
required whenever `AUTH_ENABLED=true`.
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


def test_auth_enabled_with_no_providers_enabled_only_requires_cross_cutting() -> None:
    """`AUTH_ENABLED=true` with all three provider flags off must construct
    as long as the cross-cutting secrets are set. This is the operational
    state where the auth subsystem is "on" but no provider's callback is
    reachable — every provider returns 404 at request time, same envelope
    as the master flag-off state."""
    settings = Settings(
        auth_enabled=True,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        # No per-provider secrets, no per-provider flags.
    )
    assert settings.auth_enabled is True
    assert settings.google_enabled is False
    assert settings.apple_enabled is False
    assert settings.facebook_enabled is False


def test_auth_enabled_with_empty_jwt_signing_key_rejected() -> None:
    """JWT_SIGNING_KEY is cross-cutting — required regardless of which
    providers are enabled."""
    with pytest.raises(ValidationError, match="JWT_SIGNING_KEY"):
        Settings(
            auth_enabled=True,
            jwt_signing_key="",
            refresh_token_hmac_key="key",
        )


def test_auth_enabled_with_empty_refresh_hmac_key_rejected() -> None:
    """REFRESH_TOKEN_HMAC_KEY is cross-cutting — required regardless of
    which providers are enabled."""
    with pytest.raises(ValidationError, match="REFRESH_TOKEN_HMAC_KEY"):
        Settings(
            auth_enabled=True,
            jwt_signing_key="key",
            refresh_token_hmac_key="",
        )


# ----- per-provider gating: Google ------------------------------------------


def test_google_enabled_with_empty_google_client_id_rejected() -> None:
    """An unset `GOOGLE_CLIENT_ID` would silently make every Google sign-in
    look like 'your token is invalid' (401) — actual fault is server config."""
    with pytest.raises(ValidationError, match="GOOGLE_CLIENT_ID"):
        Settings(
            auth_enabled=True,
            google_enabled=True,
            google_client_id="",
            jwt_signing_key="key",
            refresh_token_hmac_key="key",
        )


def test_google_disabled_does_not_require_google_client_id() -> None:
    """With `GOOGLE_ENABLED=false`, GOOGLE_CLIENT_ID is unused — the
    callback 404s at request time, so the validator must not demand it."""
    settings = Settings(
        auth_enabled=True,
        google_enabled=False,
        google_client_id="",
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
    )
    assert settings.google_enabled is False


# ----- per-provider gating: Apple -------------------------------------------


@pytest.mark.parametrize(
    "missing_field, env_var",
    [
        ("apple_client_id", "APPLE_CLIENT_ID"),
        ("apple_team_id", "APPLE_TEAM_ID"),
        ("apple_key_id", "APPLE_KEY_ID"),
        ("apple_private_key", "APPLE_PRIVATE_KEY"),
    ],
)
def test_apple_enabled_with_missing_apple_secret_rejected(missing_field: str, env_var: str) -> None:
    """When APPLE_ENABLED=true, every Apple secret must be set; missing one
    fails loudly at `Settings()` construction rather than at request time."""
    kwargs: dict[str, object] = dict(
        auth_enabled=True,
        apple_enabled=True,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        apple_client_id="apple-cid",
        apple_team_id="TEAM000001",
        apple_key_id="KID0000001",
        apple_private_key="-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
    )
    kwargs[missing_field] = ""
    with pytest.raises(ValidationError, match=env_var):
        Settings(**kwargs)


def test_apple_disabled_does_not_require_apple_secrets() -> None:
    """With `APPLE_ENABLED=false`, Apple secrets are unused — the callback
    404s at request time."""
    settings = Settings(
        auth_enabled=True,
        apple_enabled=False,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        # No Apple secrets at all.
    )
    assert settings.apple_enabled is False


# ----- per-provider gating: Facebook ----------------------------------------


@pytest.mark.parametrize(
    "missing_field, env_var",
    [
        ("facebook_app_id", "FACEBOOK_APP_ID"),
        ("facebook_app_secret", "FACEBOOK_APP_SECRET"),
    ],
)
def test_facebook_enabled_with_missing_facebook_secret_rejected(
    missing_field: str, env_var: str
) -> None:
    """When FACEBOOK_ENABLED=true, both APP_ID (used as the audience-equivalent
    during /debug_token validation) and APP_SECRET (used to construct the app
    access token, `{APP_ID}|{APP_SECRET}`) must be set."""
    kwargs: dict[str, object] = dict(
        auth_enabled=True,
        facebook_enabled=True,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        facebook_app_id="fb-app-id",
        facebook_app_secret="fb-app-secret",
    )
    kwargs[missing_field] = ""
    with pytest.raises(ValidationError, match=env_var):
        Settings(**kwargs)


def test_facebook_disabled_does_not_require_facebook_secrets() -> None:
    """With `FACEBOOK_ENABLED=false`, Facebook secrets are unused."""
    settings = Settings(
        auth_enabled=True,
        facebook_enabled=False,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        # No Facebook secrets at all.
    )
    assert settings.facebook_enabled is False


# ----- combined --------------------------------------------------------------


def test_auth_enabled_with_all_secrets_set_constructs() -> None:
    settings = Settings(
        auth_enabled=True,
        google_enabled=True,
        apple_enabled=True,
        facebook_enabled=True,
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
    assert settings.google_enabled is True
    assert settings.apple_enabled is True
    assert settings.facebook_enabled is True


def test_auth_enabled_lists_all_missing_secrets_for_enabled_providers() -> None:
    """When every provider is enabled and every secret is missing, the error
    names every one of them so the operator fixes the env in one shot.
    Under the new gating semantics (issue #51), this only fires when all
    three per-provider flags are also True."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            auth_enabled=True,
            google_enabled=True,
            apple_enabled=True,
            facebook_enabled=True,
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


def test_only_google_enabled_no_apple_or_facebook_secrets_required() -> None:
    """Issue #51 explicit AC: a slice-1 demo can boot with only
    GOOGLE_ENABLED=true plus the cross-cutting secrets and GOOGLE_CLIENT_ID,
    without supplying any Apple or Facebook values. This is the regression
    the new validator is designed to prevent."""
    settings = Settings(
        auth_enabled=True,
        google_enabled=True,
        apple_enabled=False,
        facebook_enabled=False,
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
        google_client_id="cid",
        # NO apple_*, NO facebook_*.
    )
    assert settings.google_enabled is True
    assert settings.apple_enabled is False
    assert settings.facebook_enabled is False
    assert settings.apple_client_id == ""
    assert settings.facebook_app_id == ""
