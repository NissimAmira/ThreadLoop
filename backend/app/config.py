from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ThreadLoop API"
    version: str = "0.1.0"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://threadloop:threadloop@localhost:5432/threadloop"
    redis_url: str = "redis://localhost:6379/0"
    meili_url: str = "http://localhost:7700"
    meili_master_key: str = "dev-master-key-change-me"

    cors_origins: str = "http://localhost:5173,http://localhost:19006"

    # --- Auth (SSO + sessions) ---
    # Master feature flag for the auth subsystem. Per RFC 0001 § Rollout plan
    # step 1, every `/api/auth/*` route returns 404 while this is False so we
    # can land the implementation behind a flag and flip it per environment.
    # Production deploys must set this to True after RFC 0001 § Rollout plan
    # step 5; flipped in staging earlier per step 3.
    auth_enabled: bool = False

    # Per-provider enablement flags. Mirror Epic #11's slice-by-slice rollout:
    # slice 1 ships Google end-to-end, slice 2 broadens to Apple, slice 3 to
    # Facebook. Each provider's secrets are required only when its own flag is
    # True (see `_require_auth_secrets_when_enabled`), and each provider's
    # callback returns 404 when its flag is False (matching the master
    # `auth_enabled` 404 path). This lets a slice-1 demo boot with only
    # `AUTH_ENABLED=true` and `GOOGLE_ENABLED=true` set, without forcing
    # dummy Apple/FB values into `.env` — the validator's loud-fail behaviour
    # for the providers the operator IS enabling is preserved per-provider.
    google_enabled: bool = False
    apple_enabled: bool = False
    facebook_enabled: bool = False

    # HS256 signing key for access JWTs and link tokens. Must be set to a
    # cryptographically random value in any non-dev environment; the default
    # value is deliberately obviously-fake so misconfigured deploys fail loudly.
    jwt_signing_key: str = "dev-jwt-signing-key-change-me"

    # HMAC-SHA-256 key applied to refresh tokens before they hit
    # `refresh_tokens.token_hash`. Distinct from `jwt_signing_key` so that
    # leaking one secret does not also let an attacker forge the other.
    refresh_token_hmac_key: str = "dev-refresh-hmac-key-change-me"

    # OIDC `aud` claim we require on every Google ID token.
    google_client_id: str = ""

    # Apple Sign In credentials. The four values together let the backend both
    # verify Apple ID tokens (`apple_client_id` is the `aud`) and sign the
    # `client_secret` JWT Apple's token endpoint expects (Apple is the only
    # provider whose `client_secret` is itself a JWT, signed with a downloaded
    # `.p8` key — see `app/auth/apple.py`). Sourced from the Apple Developer
    # portal: Identifiers → Services IDs (`apple_client_id`), team membership
    # page (`apple_team_id`), Keys → Key ID (`apple_key_id`), and the PEM
    # contents of the downloaded `.p8` (`apple_private_key`, multi-line PEM).
    apple_client_id: str = ""
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""

    # Facebook Login credentials. Unlike Google and Apple, Facebook returns an
    # access token (not an ID token) — the backend exchanges it for a profile
    # via the Graph API. Sourced from Meta for Developers → My Apps → App
    # Settings → Basic. We require both values when `auth_enabled=True` because
    # the callback validates the incoming token via `/debug_token` before
    # calling `/me`, which needs an app access token (`{APP_ID}|{APP_SECRET}`).
    facebook_app_id: str = ""
    facebook_app_secret: str = ""

    access_token_ttl_seconds: int = 15 * 60
    refresh_token_ttl_days: int = 30
    # Pending-link tokens are short-lived per RFC 0001 § Failure modes.
    link_token_ttl_seconds: int = 10 * 60

    # Cookie attributes for the refresh-token cookie. `secure=False` is
    # acceptable for local development over http://localhost only.
    refresh_cookie_name: str = "refresh_token"
    refresh_cookie_secure: bool = True
    refresh_cookie_samesite: str = "lax"
    refresh_cookie_domain: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _require_auth_secrets_when_enabled(self) -> "Settings":
        """When `auth_enabled=True`, refuse to boot with empty auth secrets
        for the providers the operator is actually enabling.

        An unset `google_client_id` would silently make every Google sign-in
        look like "your token is invalid" (401) when the real fault is server
        misconfiguration. Same for the JWT and refresh-HMAC keys: a missing
        secret would mean signed-with-the-empty-string. Fail loudly at
        `Settings()` construction instead.

        Gating per-provider rather than globally: previously this validator
        demanded all three providers' secrets whenever the master flag was
        True, which forced operators running a Google-only slice 1 demo to
        stuff dummy values into Apple/Facebook env vars just to boot — at
        which point the validator no longer caught the misconfiguration it
        was designed to. The per-provider `*_enabled` flags scope the
        loud-fail to the providers actually being shipped (issue #51,
        Epic #11). The cross-cutting `JWT_SIGNING_KEY` and
        `REFRESH_TOKEN_HMAC_KEY` are still required whenever `auth_enabled`
        is True, since every provider's session helpers reach for them.
        """
        if self.auth_enabled:
            missing: list[str] = []
            # Cross-cutting secrets — required whenever the auth subsystem is
            # on at all, regardless of which providers are enabled.
            if not self.jwt_signing_key:
                missing.append("JWT_SIGNING_KEY")
            if not self.refresh_token_hmac_key:
                missing.append("REFRESH_TOKEN_HMAC_KEY")
            if self.google_enabled and not self.google_client_id:
                missing.append("GOOGLE_CLIENT_ID")
            if self.apple_enabled:
                if not self.apple_client_id:
                    missing.append("APPLE_CLIENT_ID")
                if not self.apple_team_id:
                    missing.append("APPLE_TEAM_ID")
                if not self.apple_key_id:
                    missing.append("APPLE_KEY_ID")
                if not self.apple_private_key:
                    missing.append("APPLE_PRIVATE_KEY")
            # Facebook: both APP_ID (used as the audience-equivalent during
            # `/debug_token` validation) and APP_SECRET (used to construct the
            # app access token, `{APP_ID}|{APP_SECRET}`) are required when
            # Facebook is enabled. Without the secret, every Facebook sign-in
            # would silently 401 at /debug_token rather than telling the
            # operator the real cause.
            if self.facebook_enabled:
                if not self.facebook_app_id:
                    missing.append("FACEBOOK_APP_ID")
                if not self.facebook_app_secret:
                    missing.append("FACEBOOK_APP_SECRET")
            if missing:
                raise ValueError(
                    "AUTH_ENABLED=true but the following auth secrets are unset: "
                    + ", ".join(missing)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
