"""宏曦标书 - Application Configuration.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "宏曦标书"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database (async + sync)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hongxi_bidding"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/hongxi_bidding"

    # Auth
    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # AI / DeepSeek
    AI_PROVIDER: str = "deepseek"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    AI_TEMPERATURE: float = 0.7
    AI_MAX_TOKENS: int = 4096

    # File paths
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "outputs"
    TEMPLATE_DIR: str = "templates"

    # Notification webhooks (optional)
    WECOM_WEBHOOK_URL: str = ""
    DINGTALK_WEBHOOK_URL: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]


settings = Settings()
