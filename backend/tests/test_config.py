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
        )
    msg = str(exc_info.value)
    assert "GOOGLE_CLIENT_ID" in msg
    assert "JWT_SIGNING_KEY" in msg
    assert "REFRESH_TOKEN_HMAC_KEY" in msg


def test_auth_enabled_with_all_secrets_set_constructs() -> None:
    settings = Settings(
        auth_enabled=True,
        google_client_id="cid",
        jwt_signing_key="key",
        refresh_token_hmac_key="key",
    )
    assert settings.auth_enabled is True
