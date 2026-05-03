"""Single source of truth for `Settings(...)` construction in auth tests.

Why a factory: every time `Settings` gains a new required-when-`auth_enabled`
field (Apple in #15, Facebook in #16, more to come in #17), the test suite
historically had to update each ad-hoc `Settings(...)` call site
independently. Missing one silently produced an auth-disabled-defaults shape
with no compile or runtime signal — tests would pass against the wrong
configuration. This module owns the defaults; touch it once when the
contract changes.

Usage:

    from tests.auth._test_settings import make_test_settings

    settings = make_test_settings(database_url=pg_url, refresh_cookie_secure=False)

The Apple PEM default is a non-cryptographic placeholder string. `Settings`
only validates the field is non-empty (PEM contents are validated by the
Apple `client_secret` signer when actually used). Tests that exercise the
Apple branch end-to-end pass `apple_private_key=apple_p8_pem` as an override
to inject a real ES256 PEM from the `apple_p8_pem` fixture.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings

_FAKE_APPLE_PEM = "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n"


def make_test_settings(**overrides: Any) -> Settings:
    """Build a `Settings(auth_enabled=True, ...)` populated with sane test
    defaults for every required-when-auth-enabled field.

    Pass per-test overrides as kwargs — the most common ones are
    `database_url=pg_url` (point at the Testcontainers Postgres) and
    `refresh_cookie_secure=False` (TestClient runs over plain HTTP).

    To exercise the auth-disabled path, pass `auth_enabled=False`; the other
    secrets stay populated so the same call works for both branches without
    re-listing every field.
    """
    defaults: dict[str, Any] = {
        "auth_enabled": True,
        # Per-provider flags default to True so existing integration tests
        # that exercise each provider's callback don't have to opt in
        # individually. Tests that want to assert the disabled-provider 404
        # path pass e.g. `apple_enabled=False` as an override.
        "google_enabled": True,
        "apple_enabled": True,
        "facebook_enabled": True,
        "jwt_signing_key": "test-jwt-signing-key",
        "refresh_token_hmac_key": "test-hmac-key",
        "google_client_id": "test-google-client-id.apps.googleusercontent.com",
        "apple_client_id": "test-apple-client-id",
        "apple_team_id": "TESTTEAM01",
        "apple_key_id": "TESTKID0001",
        "apple_private_key": _FAKE_APPLE_PEM,
        "facebook_app_id": "test-facebook-app-id",
        "facebook_app_secret": "test-facebook-app-secret",
        "link_token_ttl_seconds": 600,
    }
    defaults.update(overrides)
    return Settings(**defaults)
