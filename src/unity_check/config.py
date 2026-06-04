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
    redis_url: str = Field(alias="REDIS_URL")
    github_webhook_secret: str = Field(default="", alias="GITHUB_WEBHOOK_SECRET")
    github_remote_repo: str = Field(alias="GITHUB_REMOTE_REPO")

    llm_provider: str = Field(default="deepseek", alias="LLM_PROVIDER")
    llm_base_url: str = Field(default="https://api.deepseek.com", alias="LLM_BASE_URL")
    llm_model: str = Field(default="deepseek-chat", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    git_ssh_key_path: str = Field(default="", alias="GIT_SSH_KEY_PATH")

    roslyn_service_url: str = Field(default="http://roslyn:8080", alias="ROSLYN_SERVICE_URL")
    default_analyze_paths: str = Field(default="Assets/Scripts", alias="DEFAULT_ANALYZE_PATHS")

    git_clone_base_dir: str = Field(default="./repos", alias="GIT_CLONE_BASE_DIR")

    notify_risk_threshold: str = Field(default="medium", alias="NOTIFY_RISK_THRESHOLD")
    notify_score_threshold: float = Field(default=70.0, alias="NOTIFY_SCORE_THRESHOLD")


@lru_cache()
def get_settings() -> Settings:
    return Settings()
