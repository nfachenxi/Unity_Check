from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="Unity Check", alias="APP_NAME")
    app_env: str = Field(default="dev", alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    app_log_level: str = Field(default="INFO", alias="APP_LOG_LEVEL")

    database_url: str = Field(alias="DATABASE_URL")

    llm_base_url: str = Field(default="https://api.deepseek.com", alias="LLM_BASE_URL")
    llm_model: str = Field(default="deepseek-chat", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    git_clone_base_dir: str = Field(default="./repos", alias="GIT_CLONE_BASE_DIR")
    git_ssh_key_path: str | None = Field(default=None, alias="GIT_SSH_KEY_PATH")

    generic_webhook_secret: str | None = Field(default=None, alias="GENERIC_WEBHOOK_SECRET")

    # GitHub mirrors (comma-separated in env, e.g. GITHUB_MIRROR_URLS=https://a.com,https://b.com)
    # Order does not matter — the fastest is auto-selected before each clone.
    github_mirror_urls: list[str] = Field(default_factory=list, alias="GITHUB_MIRROR_URLS")
    # Kept for backward compatibility; takes effect only when GITHUB_MIRROR_URLS is not set
    github_mirror_url: str | None = Field(default=None, alias="GITHUB_MIRROR_URL")

    task_worker_interval: int = Field(default=2, alias="TASK_WORKER_INTERVAL")
    task_processing_timeout: int = Field(default=1800, alias="TASK_PROCESSING_TIMEOUT")

    frontend_dist_dir: str = Field(default="./frontend/dist", alias="FRONTEND_DIST_DIR")

    @field_validator("github_mirror_urls", mode="before")
    @classmethod
    def _parse_mirror_urls(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return v
        return []

    @model_validator(mode="after")
    def _resolve_mirrors(self) -> "Settings":
        if not self.github_mirror_urls and self.github_mirror_url:
            self.github_mirror_urls = [self.github_mirror_url]
        return self


@lru_cache()
def get_settings() -> Settings:
    return Settings()
