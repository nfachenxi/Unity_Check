from unittest.mock import patch

import pytest
from sqlalchemy import text

from unity_check.models import GithubEvent
from unity_check.tasks import (
    _resolve_clone_url,
    process_github_event,
)


@pytest.fixture(autouse=True)
def _override_session_local(monkeypatch, session):
    """Make SessionLocal return the test session so task can use it."""
    monkeypatch.setattr("unity_check.tasks.SessionLocal", lambda: session)


# ---------------------------------------------------------------------------
# _resolve_clone_url
# ---------------------------------------------------------------------------
class TestResolveCloneUrl:
    def test_from_payload_push(self, monkeypatch):
        event = GithubEvent(
            event_type="push",
            payload={"repository": {"ssh_url": "git@github.com:o/r.git"}},
        )
        url = _resolve_clone_url(event)
        assert url == "git@github.com:o/r.git"

    def test_fallback_to_config(self, monkeypatch):
        from unity_check.config import get_settings

        monkeypatch.setattr(
            get_settings(), "github_remote_repo", "git@config/repo.git"
        )
        event = GithubEvent(event_type="push", payload={})
        url = _resolve_clone_url(event)
        assert url == "git@config/repo.git"

    def test_none_when_both_missing(self, monkeypatch):
        from unity_check.config import get_settings

        monkeypatch.setattr(
            get_settings(), "github_remote_repo", ""
        )
        event = GithubEvent(event_type="push", payload={})
        url = _resolve_clone_url(event)
        assert url is None


# ---------------------------------------------------------------------------
# process_github_event
# ---------------------------------------------------------------------------
class TestProcessGithubEvent:
    def test_not_found(self, session):
        result = process_github_event(99999)
        assert result["status"] == "not_found"

    def test_success_path(self, session):
        event = GithubEvent(
            delivery_id="task-test-success",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            status="queued",
        )
        session.add(event)
        session.commit()
        event_id = event.id
        session.expunge(event)

        result = process_github_event(event_id)
        assert result["status"] == "success"
        assert result["risk_level"] == "low"

        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.status == "success"
        assert reloaded.risk_level == "low"

    def test_exception_sets_failed(self, session):
        event = GithubEvent(
            delivery_id="task-test-fail",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            status="queued",
        )
        session.add(event)
        session.commit()
        event_id = event.id
        session.expunge(event)

        with patch(
            "unity_check.tasks.run_evaluation_pipeline",
            side_effect=RuntimeError("LLM timeout"),
        ):
            result = process_github_event(event_id)

        assert result["status"] == "failed"

        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.status == "failed"
        assert "LLM timeout" in (reloaded.error_message or "")

    def test_skips_git_when_no_clone_url(self, session, monkeypatch):
        """When no clone URL can be resolved, git is skipped but LLM still runs."""
        from unity_check.config import get_settings

        monkeypatch.setattr(get_settings(), "github_remote_repo", "")
        event = GithubEvent(
            delivery_id="task-no-git",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            status="queued",
        )
        session.add(event)
        session.commit()
        event_id = event.id
        session.expunge(event)

        result = process_github_event(event_id)
        assert result["status"] == "success"
        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.diff_content is None
        assert reloaded.diff_size is None

    def test_error_message_appended_on_second_failure(self, session):
        """When an event already has an error_message, new errors append."""
        event = GithubEvent(
            delivery_id="task-append-err",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            status="queued",
            error_message="prior: git timeout",
        )
        session.add(event)
        session.commit()
        event_id = event.id
        session.expunge(event)

        with patch(
            "unity_check.tasks.run_evaluation_pipeline",
            side_effect=RuntimeError("LLM crash"),
        ):
            result = process_github_event(event_id)

        assert result["status"] == "failed"
        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert "prior: git timeout" in (reloaded.error_message or "")
        assert "LLM crash" in (reloaded.error_message or "")
