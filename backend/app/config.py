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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
