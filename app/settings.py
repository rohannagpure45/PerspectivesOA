"""Runtime settings, sourced from .env via pydantic-settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    sp_base_url: str = "https://secure.simplepractice.com"
    sp_api_version: str = "2025-03-21"
    sp_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    )
    sp_client_hashed_id: str = "0c39dadff6972e0f"
    sp_session_cookie: str = ""
    sp_force_fixtures: bool = False
    sp_overview_page_size: int = 20

    perspectives_fixture_dir: str = "fixtures"

    database_url: str = "postgresql+asyncpg://perspectives:perspectives@localhost:5432/perspectives_oa"
    database_enabled: bool = False

    log_level: str = "INFO"

    @property
    def fixture_dir(self) -> Path:
        p = Path(self.perspectives_fixture_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def use_fixtures(self) -> bool:
        return self.sp_force_fixtures or not self.sp_session_cookie


@lru_cache
def get_settings() -> Settings:
    return Settings()
