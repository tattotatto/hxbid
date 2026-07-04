"""宏曦标书 - Application Configuration.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from pydantic import field_validator
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

    # AI / Multi-model support (DeepSeek, OpenAI, TongYi)
    AI_PROVIDER: str = "deepseek"

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # OpenAI (GPT-4o, GPT-4, etc.)
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"

    # TongYi / Qwen (阿里通义千问, OpenAI-compatible endpoint)
    TONGYI_API_KEY: str = ""
    TONGYI_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    TONGYI_MODEL: str = "qwen3.7-plus"

    AI_MODEL_OVERRIDE: str = ""  # Runtime model override (set via admin API / settings UI)

    AI_TEMPERATURE: float = 0.7
    AI_MAX_TOKENS: int = 4096

    # File paths
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "outputs"
    TEMPLATE_DIR: str = "templates"

    # ChromaDB vector store for semantic chapter search
    CHROMA_PERSIST_DIR: str = "./chroma_data"
    CHROMA_COLLECTION_NAME: str = "bid_chapters"
    EMBEDDING_MODEL: str = "BAAI/bge-small-zh-v1.5"
    VECTOR_STORE_ENABLED: bool = True

    # Notification webhooks (optional)
    WECOM_WEBHOOK_URL: str = ""
    DINGTALK_WEBHOOK_URL: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v


settings = Settings()
