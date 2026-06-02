from unittest.mock import patch

import pytest
from sqlalchemy import text

from unity_check.models import GithubEvent
from unity_check.tasks import build_event_summary, process_github_event


@pytest.fixture(autouse=True)
def _override_session_local(monkeypatch, session):
    """Make SessionLocal return the test session so task can use it."""
    monkeypatch.setattr("unity_check.tasks.SessionLocal", lambda: session)


class TestBuildEventSummary:
    def test_push_summary(self):
        event = GithubEvent(
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}, {}]},
        )
        summary = build_event_summary(event)
        assert "push to refs/heads/main" in summary
        assert "commits=2" in summary

    def test_pr_summary(self):
        event = GithubEvent(
            event_type="pull_request",
            action="opened",
            payload={
                "pull_request": {"number": 42, "title": "Fix bug"},
            },
        )
        summary = build_event_summary(event)
        assert "pull_request #42" in summary
        assert "action=opened" in summary
        assert "Fix bug" in summary

    def test_pr_summary_fallback_when_pr_key_missing(self):
        event = GithubEvent(
            event_type="pull_request",
            action="synchronize",
            payload={"number": 99},
        )
        summary = build_event_summary(event)
        assert "#99" in summary

    def test_unknown_event_type_summary(self):
        event = GithubEvent(event_type="issues", action="opened", payload={})
        summary = build_event_summary(event)
        assert "issues" in summary


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
        session.expunge(event)  # task creates its own session

        result = process_github_event(event_id)
        assert result["status"] == "success"
        assert result["risk_level"] == "low"

        # Re-query from DB
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
            "unity_check.tasks.evaluate_with_llm",
            side_effect=RuntimeError("LLM timeout"),
        ):
            result = process_github_event(event_id)

        assert result["status"] == "failed"

        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.status == "failed"
        assert "LLM timeout" in (reloaded.error_message or "")
