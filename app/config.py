import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    APIFY_API_TOKEN: str
    APIFY_ACTOR_ID: str = "harvestapi~linkedin-company-posts"
    MAX_POSTS_LIMIT: int = 100
    MAX_REACTIONS_LIMIT: int = 50
    MAX_COMMENTS_LIMIT: int = 50

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Allow extra env vars like OPENAI_API_KEY
    )


def get_settings() -> Settings:
    """Get settings instance (no caching to pick up changes)."""
    return Settings()
