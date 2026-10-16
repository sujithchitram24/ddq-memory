"""Application settings, read from environment variables (12-factor style).

Validation happens at startup: a missing DATABASE_URL fails immediately with a clear
error instead of on the first request.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

PROCESSOR_VERSION = "2026.10.1"  # bump when extraction/normalisation logic changes


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    storage_dir: str = "./storage"
    max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB
    max_unzipped_bytes: int = 100 * 1024 * 1024  # zip-bomb guard for DOCX/XLSX
    job_lease_seconds: int = 300
    job_max_attempts: int = 3
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-5-5"


@lru_cache
def get_settings() -> Settings:
    return Settings()
