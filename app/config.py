from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_webhook_secret: str                     # required — app won't boot without it
    github_token: str | None = None                # optional until 3b actually fetches
    anthropic_api_key: str | None = None           # optional — only the worker needs it
    database_url: str = "postgresql://pruser:prpass@localhost:5432/prreviewer"
    redis_url: str = "redis://localhost:6379"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


database_url: str = "postgresql://pruser:prpass@localhost:5432/prreviewer"