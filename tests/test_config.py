import os

import pytest

from unity_check.config import Settings, get_settings


class TestSettingsLoading:
    """Verify Settings loads from .env and respects defaults."""

    def test_loads_from_dotenv(self):
        """Settings should load from the project .env file."""
        settings = Settings()
        # Required fields must have values (either from .env or env var)
        assert settings.database_url
        assert settings.redis_url
        assert settings.github_remote_repo

    def test_required_database_url_raises_without_env(self, monkeypatch):
        """Settings should raise if DATABASE_URL is not set."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        with pytest.raises(Exception):  # ValidationError
            Settings(_env_file=None)

    def test_defaults_are_correct(self, monkeypatch):
        """Verify default field values match spec."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        settings = Settings(_env_file=None)
        assert settings.app_name == "Unity Check"
        assert settings.app_env == "dev"
        assert settings.app_host == "0.0.0.0"
        assert settings.app_port == 8000
        assert settings.app_log_level == "INFO"
        assert settings.llm_provider == "deepseek"
        assert settings.llm_base_url == "https://api.deepseek.com"
        assert settings.llm_model == "deepseek-chat"

    def test_extra_fields_ignored(self, monkeypatch):
        """extra='ignore' should suppress errors for unknown env vars."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        monkeypatch.setenv("UNKNOWN_CUSTOM_VAR", "hello")
        # Should not raise despite unknown var
        settings = Settings(_env_file=None)
        assert settings.app_name == "Unity Check"

    def test_git_ssh_key_path_defaults_to_empty(self, monkeypatch):
        """GIT_SSH_KEY_PATH should default to ''."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        settings = Settings(_env_file=None)
        assert settings.git_ssh_key_path == ""

    def test_git_ssh_key_path_from_env(self, monkeypatch):
        """GIT_SSH_KEY_PATH should be picked up from env."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        monkeypatch.setenv("GIT_SSH_KEY_PATH", "/home/user/.ssh/id_rsa")
        settings = Settings(_env_file=None)
        assert settings.git_ssh_key_path == "/home/user/.ssh/id_rsa"

    def test_roslyn_service_url_defaults(self, monkeypatch):
        """ROSLYN_SERVICE_URL should default to http://roslyn:8080."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        settings = Settings(_env_file=None)
        assert settings.roslyn_service_url == "http://roslyn:8080"

    def test_roslyn_service_url_from_env(self, monkeypatch):
        """ROSLYN_SERVICE_URL should be read from env."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        monkeypatch.setenv("ROSLYN_SERVICE_URL", "http://custom:9000")
        settings = Settings(_env_file=None)
        assert settings.roslyn_service_url == "http://custom:9000"

    def test_default_analyze_paths_defaults(self, monkeypatch):
        """DEFAULT_ANALYZE_PATHS should default to Assets/Scripts."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        settings = Settings(_env_file=None)
        assert settings.default_analyze_paths == "Assets/Scripts"

    def test_default_analyze_paths_from_env(self, monkeypatch):
        """DEFAULT_ANALYZE_PATHS should be read from env."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        monkeypatch.setenv("REDIS_URL", "redis://test")
        monkeypatch.setenv("GITHUB_REMOTE_REPO", "git@test")
        monkeypatch.setenv("DEFAULT_ANALYZE_PATHS", "Path/A,Path/B")
        settings = Settings(_env_file=None)
        assert settings.default_analyze_paths == "Path/A,Path/B"


class TestGetSettingsCache:
    """Verify @lru_cache() works on get_settings()."""

    def test_two_calls_return_same_instance(self):
        """get_settings() should be cached — two calls return identical object."""
        # We must call __wrapped__ to bypass a potentially memoized call from
        # other tests. Instead, call twice and verify.
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_cache_info_reflects_hits(self):
        """After a second call, cache_info should show hits >= 1."""
        info = get_settings.cache_info()
        assert info.hits >= 1, f"Expected cache hits >= 1, got {info}"
