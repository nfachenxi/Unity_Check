"""Tests for webhook parsing and event endpoints (simplified)."""

import hashlib
import hmac
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unity_check.db import get_db
from unity_check.main import app
from unity_check.models import EvaluationRound, GithubEvent
from unity_check import repository_service


@pytest.fixture(autouse=True)
def _register_test_repo(session):
    """Ensure test/repo is registered so webhook tests pass."""
    try:
        repo = repository_service.create_repository(
            session,
            name="test/repo",
            clone_url="https://github.com/test/repo.git",
            is_active=True,
        )
        session.flush()
    except ValueError:
        repo = repository_service.get_repository_by_name(session, "test/repo")
    return repo


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch, session):
    """Override FastAPI dependencies for testing."""
    app.dependency_overrides[get_db] = lambda: session

    # Mock git operations so webhook doesn't try real clone/fetch
    def fake_ensure_bare_repo(clone_url, ssh_key_path=None):
        return "/fake/path.git"

    def fake_get_diff(bare_repo_path, before_sha, after_sha):
        return "diff --git a/A.cs b/A.cs\n+code\n"

    monkeypatch.setattr("unity_check.main.ensure_bare_repo", fake_ensure_bare_repo)
    monkeypatch.setattr("unity_check.main.get_diff", fake_get_diff)

    yield
    app.dependency_overrides.clear()


@pytest.fixture()
def client():
    return TestClient(app)


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebhookPing:
    def test_ping_returns_ok(self, client):
        resp = client.post(
            "/webhook/github",
            headers={"X-GitHub-Event": "ping"},
            content=b"{}",
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_ping_with_signature_header_still_ok(self, client):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": "sha256=abcdef",
            },
            content=b"{}",
        )
        assert resp.status_code == 200


class TestWebhookPush:
    @staticmethod
    def _valid_push_payload():
        return {
            "ref": "refs/heads/main",
            "before": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "after": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }

    @staticmethod
    def _valid_pr_payload():
        return {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "title": "Test PR",
                "base": {"sha": "base-sha-40-chars-base-sha-40-charsss"},
                "head": {
                    "sha": "head-sha-40-chars-head-sha-40-charsss",
                    "repo": {"clone_url": "https://github.com/test/repo.git"},
                },
            },
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }

    def test_push_returns_200(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-push-001",
            },
            content=json.dumps(self._valid_push_payload()).encode(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "event_id" in data

    def test_pull_request_returns_200(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-pr-001",
            },
            content=json.dumps(self._valid_pr_payload()).encode(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"

    def test_events_persisted_to_db(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-persist-001",
            },
            content=json.dumps(self._valid_push_payload()).encode(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        events_resp = client.get("/api/events").json()
        ids = [e["id"] for e in events_resp["items"]]
        assert int(data["event_id"]) in ids

    def test_sha_extracted_for_push(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-push-sha",
            },
            content=json.dumps(self._valid_push_payload()).encode(),
        )
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]
        detail = client.get(f"/api/events/{event_id}").json()
        assert detail["after_sha"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        assert detail["before_sha"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_sha_extracted_for_pr(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-pr-sha",
            },
            content=json.dumps(self._valid_pr_payload()).encode(),
        )
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]
        detail = client.get(f"/api/events/{event_id}").json()
        assert detail["before_sha"] == "base-sha-40-chars-base-sha-40-charsss"
        assert detail["after_sha"] == "head-sha-40-chars-head-sha-40-charsss"

    def test_idempotent_by_delivery_id(self, client, session):
        headers = {
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-idem-001",
        }
        body = json.dumps(self._valid_push_payload()).encode()
        r1 = client.post("/webhook/github", headers=headers, content=body)
        assert r1.status_code == 200
        session.flush()
        r2 = client.post("/webhook/github", headers=headers, content=body)
        assert r2.status_code == 200
        assert r2.json()["event_id"] == r1.json()["event_id"]


class TestWebhookValidation:
    def test_unsupported_event_type_returns_400(self, client):
        resp = client.post(
            "/webhook/github",
            headers={"X-GitHub-Event": "issues"},
            content=b"{}",
        )
        assert resp.status_code == 400

    def test_invalid_json_returns_400(self, client):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-badjson",
            },
            content=b"not json",
        )
        assert resp.status_code == 400


class TestEventDetail:
    def _make_push_event(self, client):
        payload = {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": f"delivery-detail-{uuid4().hex[:8]}",
            },
            content=json.dumps(payload).encode(),
        )
        assert resp.status_code == 200
        return resp.json()["event_id"]

    def test_returns_full_event(self, client, session):
        event_id = self._make_push_event(client)
        detail = client.get(f"/api/events/{event_id}").json()
        assert detail["id"] == int(event_id)
        assert detail["event_type"] == "push"
        assert "diff_content" in detail
        assert "clone_path" in detail
        assert "dimension_a_score" in detail
        assert "dimension_b_score" in detail
        assert "final_risk_level" in detail
        assert "updated_at" in detail

    def test_includes_assessment_when_requested(self, client, session):
        event = GithubEvent(
            delivery_id="assess-include",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        session.add(EvaluationRound(
            event_id=event_id, round_number=1, round_type="functionality_best_practices",
            file_path="A.cs", status="success", input_summary={}, output_data={"total": 0},
        ))
        session.commit()

        resp = client.get(f"/api/events/{event_id}?include=assessment")
        assert resp.status_code == 200
        data = resp.json()
        assert "assessment" in data
        assert len(data["assessment"]["rounds"]) == 1

    def test_nonexistent_event_returns_404(self, client):
        resp = client.get("/api/events/99999")
        assert resp.status_code == 404


class TestWebhookSecurity:
    """Webhook signature verification and repo access control."""

    UNREGISTERED_PAYLOAD = {
        "ref": "refs/heads/main",
        "before": "a" * 40,
        "after": "b" * 40,
        "repository": {"full_name": "evil/unregistered", "clone_url": "https://github.com/evil/unregistered.git"},
    }

    def test_unregistered_repo_returns_404(self, client):
        resp = client.post(
            "/webhook/github",
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-evil"},
            content=json.dumps(self.UNREGISTERED_PAYLOAD).encode(),
        )
        assert resp.status_code == 404
        assert "not registered" in resp.json()["detail"]

    def test_missing_signature_returns_401(self, client, session, _register_test_repo):
        """When repo has a webhook_secret, missing signature is rejected."""
        # Give test/repo a secret
        repository_service.update_repository(
            session, _register_test_repo.id, webhook_secret="my-secret"
        )
        session.commit()
        payload = {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        resp = client.post(
            "/webhook/github",
            headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "delivery-nosig"},
            content=json.dumps(payload).encode(),
        )
        assert resp.status_code == 401
        assert "Missing" in resp.json()["detail"]

    def test_invalid_signature_returns_401(self, client, session, _register_test_repo):
        repository_service.update_repository(
            session, _register_test_repo.id, webhook_secret="my-secret"
        )
        session.commit()
        payload = {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        body = json.dumps(payload).encode()
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-badsig",
                "X-Hub-Signature-256": "sha256=0000000000000000000000000000000000000000",
            },
            content=body,
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]

    def test_valid_signature_passes(self, client, session, _register_test_repo):
        repository_service.update_repository(
            session, _register_test_repo.id, webhook_secret="my-secret"
        )
        session.commit()
        payload = {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        body = json.dumps(payload).encode()
        sig = "sha256=" + hmac.new(b"my-secret", body, hashlib.sha256).hexdigest()
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-validsig",
                "X-Hub-Signature-256": sig,
            },
            content=body,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"


class TestWebhookBranchFilter:
    """Branch filter skips/processes webhooks based on branch_pattern."""

    def test_branch_filter_skips_unmatched(self, client, session, _register_test_repo):
        repository_service.update_repository(
            session, _register_test_repo.id, branch_filter='["main"]'
        )
        session.commit()
        payload = {
            "ref": "refs/heads/feature/x",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-filter-skip",
            },
            content=json.dumps(payload).encode(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_branch_filter_allows_matched(self, client, session, _register_test_repo):
        repository_service.update_repository(
            session, _register_test_repo.id, branch_filter='["main", "release/*"]'
        )
        session.commit()
        payload = {
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "repository": {"full_name": "test/repo", "clone_url": "https://github.com/test/repo.git"},
        }
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-filter-allow",
            },
            content=json.dumps(payload).encode(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
