from functools import lru_cache

from pydantic import Field
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
    github_mirror_url: str | None = Field(default=None, alias="GITHUB_MIRROR_URL")

    task_worker_interval: int = Field(default=2, alias="TASK_WORKER_INTERVAL")
    task_processing_timeout: int = Field(default=1800, alias="TASK_PROCESSING_TIMEOUT")

    frontend_dist_dir: str = Field(default="./frontend/dist", alias="FRONTEND_DIST_DIR")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
