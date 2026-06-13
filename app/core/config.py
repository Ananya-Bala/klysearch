"""
core/config.py
--------------
Single source of truth for all runtime configuration.
Uses pydantic-settings to validate env vars at startup — fail fast,
never silently run with bad config.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_TITLE: str = "Investment Research Dashboard"
    APP_VERSION: str = "1.0.0"

    # ── Database ─────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── JWT ──────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── CORS ─────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # ── Phase 3A: AI Research Engine ─────────────────────────────────────
    # Required for AI report generation. Set in .env.
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Optional — enables news enrichment. Leave blank to skip gracefully.
    NEWS_API_KEY: str = ""
    NEWS_API_BASE_URL: str = "https://newsapi.org/v2"

    # Max age of a cached report before re-analysis is triggered (minutes).
    # Prevents duplicate OpenAI calls on rapid repeated requests.
    REPORT_CACHE_MINUTES: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_parse_none_str="None",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Cached settings instance — lru_cache means one load per process,
    not one per request.
    """
    return Settings()


# Convenience alias used throughout the app
settings = get_settings()
