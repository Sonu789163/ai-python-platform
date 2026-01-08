"""
Environment-based configuration management.
Supports sandbox, dev, and prod environments.
"""
import os
from typing import Literal, Optional
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Environment
    APP_ENV: Literal["sandbox", "dev", "prod"] = "sandbox"
    APP_NAME: str = "AI Python Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # API Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_WORKERS: int = 4
    
    # Redis Configuration
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""
    
    # Celery Configuration
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""
    CELERY_TASK_SERIALIZER: str = "json"
    CELERY_RESULT_SERIALIZER: str = "json"
    CELERY_ACCEPT_CONTENT: list = ["json"]
    CELERY_TIMEZONE: str = "UTC"
    CELERY_ENABLE_UTC: bool = True
    
    # MongoDB Configuration
    MONGO_URI: str = "mongodb+srv://sonuv:Sonu12345@cluster0.makyp.mongodb.net/"
    MONGO_DB_NAME: str = "pdf-summarizer"
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    
    # AI/ML Settings
    MAX_CHUNK_SIZE: int = 4000
    CHUNK_OVERLAP: int = 800
    EMBEDDING_DIMENSION: int = 3072  # text-embedding-3-large
    EMBEDDING_MODEL: str = "text-embedding-3-large"
    
    # OpenAI
    OPENAI_API_KEY: str = ""
    SUMMARY_MODEL: str = "gpt-4.1-mini"
    
    # Pinecone
    PINECONE_API_KEY: str = ""
    PINECONE_ENVIRONMENT: str = ""
    PINECONE_DRHP_INDEX: str = "drhpdocuments"  # Fixed name
    PINECONE_RHP_INDEX: str = "rhpdocuments"
    PINECONE_DRHP_HOST: str = "https://drhpdocuments-w5m6qxe.svc.aped-4627-b74a.pinecone.io"
    PINECONE_RHP_HOST: str = "https://rhpdocuments-w5m6qxe.svc.aped-4627-b74a.pinecone.io"
    PERPLEXITY_API_KEY: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    
    # Backend URLs
    BACKEND_STATUS_URL: str = "https://smart-rhtp-backend-2.onrender.com/api/documents/upload-status/update"
    REPORT_CREATE_URL: str = "https://smart-rhtp-backend-2.onrender.com/api/reports/create-report"
    REPORT_STATUS_UPDATE_URL: str = "https://smart-rhtp-backend-2.onrender.com/api/reports/report-status/update"
    CHAT_STATUS_UPDATE_URL: str = "https://smart-rhtp-backend-2.onrender.com/api/chats/chat-status/update"
    SUMMARY_CREATE_URL: str = "http://localhost:5000/api/summaries/create"
    SUMMARY_STATUS_UPDATE_URL: str = "http://localhost:5000/api/summaries/summary-status/update"

    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-configure Redis URLs if not set
        if not self.CELERY_BROKER_URL:
            redis_password = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.CELERY_BROKER_URL = (
                f"redis://{redis_password}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
            )
        if not self.CELERY_RESULT_BACKEND:
            redis_password = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
            self.CELERY_RESULT_BACKEND = (
                f"redis://{redis_password}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
            )
    
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.APP_ENV == "prod"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.APP_ENV == "dev"
    
    @property
    def is_sandbox(self) -> bool:
        """Check if running in sandbox environment."""
        return self.APP_ENV == "sandbox"


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached settings instance.
    Uses lru_cache to ensure settings are loaded only once.
    """
    return Settings()


# Global settings instance
settings = get_settings()
