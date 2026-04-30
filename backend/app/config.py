from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
