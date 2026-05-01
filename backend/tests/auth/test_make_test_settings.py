"""Regression coverage for the `make_test_settings` factory.

The factory is used by every auth integration test, so a regression here
poisons the whole suite. These tests pin the contract: defaults produce a
fully-populated auth-enabled `Settings`, overrides apply, and missing a
required-when-auth-enabled field still fails loudly (proving we haven't
accidentally papered over the validator).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.auth._test_settings import make_test_settings


def test_default_is_auth_enabled_and_constructs_cleanly() -> None:
    """No-arg call returns an auth-enabled `Settings` populated with every
    required secret — i.e. the validator in `Settings._require_auth_secrets_when_enabled`
    accepts the defaults."""
    settings = make_test_settings()
    assert settings.auth_enabled is True
    assert settings.google_client_id
    assert settings.apple_client_id
    assert settings.apple_team_id
    assert settings.apple_key_id
    assert settings.apple_private_key
    assert settings.facebook_app_id
    assert settings.facebook_app_secret
    assert settings.jwt_signing_key
    assert settings.refresh_token_hmac_key


def test_overrides_apply() -> None:
    """Per-test overrides win over factory defaults, including the
    `auth_enabled` flag itself (used by the auth-disabled 404 tests)."""
    settings = make_test_settings(
        database_url="postgresql+psycopg://x:x@elsewhere/db",
        refresh_cookie_secure=False,
        link_token_ttl_seconds=42,
    )
    assert settings.database_url == "postgresql+psycopg://x:x@elsewhere/db"
    assert settings.refresh_cookie_secure is False
    assert settings.link_token_ttl_seconds == 42


def test_auth_disabled_override_works() -> None:
    """Auth-disabled path: validator does not run, but the call still
    succeeds without re-listing every secret."""
    settings = make_test_settings(auth_enabled=False)
    assert settings.auth_enabled is False
    # Other defaults still populated — caller does not need to clear them.
    assert settings.google_client_id


def test_factory_does_not_mask_validator() -> None:
    """If a caller blanks a required-when-auth-enabled field, `Settings`
    must still raise. Proves the factory is a sane-defaults helper, not a
    safety bypass."""
    with pytest.raises(ValidationError):
        make_test_settings(facebook_app_id="")
    with pytest.raises(ValidationError):
        make_test_settings(facebook_app_secret="")
    with pytest.raises(ValidationError):
        make_test_settings(google_client_id="")
