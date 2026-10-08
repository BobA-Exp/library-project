from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openlibrary_base_url: str = "https://openlibrary.org"
    database_path: Path = Path("data/library.db")
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: float = 30.0
    report_output_dir: Path = Path("data/outputs")
    log_level: str = "INFO"
    log_file: Path = Path("logs/library.log")


@lru_cache
def get_settings() -> Settings:
    return Settings()
