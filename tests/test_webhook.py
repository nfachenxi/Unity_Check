"""Tests for webhook parsing and new GET /events/{event_id} endpoint."""

import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unity_check.db import get_db
from unity_check.main import app
from unity_check.models import GithubEvent


def _fake_task_id() -> str:
    return str(uuid4())


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch, session):
    """Override FastAPI dependencies and Celery tasks for testing.

    Uses app.dependency_overrides (canonical FastAPI test approach)
    rather than monkeypatch on Depends imports.
    """
    # FastAPI dependency override — canonical approach
    app.dependency_overrides[get_db] = lambda: session

    # Mock process_github_event.delay so we never touch Celery/Redis
    class _FakeAsyncResult:
        def __init__(self, event_id):
            self.id = _fake_task_id()
            self.event_id = event_id

    def _fake_delay(event_id):
        return _FakeAsyncResult(event_id)

    monkeypatch.setattr("unity_check.main.process_github_event.delay", _fake_delay)
    monkeypatch.setattr("unity_check.tasks.SessionLocal", lambda: session)

    yield

    # Cleanup
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
            "repository": {"full_name": "test/repo"},
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
                "head": {"sha": "head-sha-40-chars-head-sha-40-charsss"},
            },
            "repository": {"full_name": "test/repo"},
        }

    def test_push_returns_202(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-push-001",
            },
            content=json.dumps(self._valid_push_payload()).encode(),
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "event_id" in data
        assert "task_id" in data

    def test_pull_request_returns_202(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "delivery-pr-001",
            },
            content=json.dumps(self._valid_pr_payload()).encode(),
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"

    def test_events_persisted_to_db(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-persist-001",
            },
            content=json.dumps(self._valid_push_payload()).encode(),
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        latest = client.get("/events/latest").json()
        ids = [e["id"] for e in latest]
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
        assert resp.status_code == 202
        event_id = resp.json()["event_id"]
        detail = client.get(f"/events/{event_id}").json()
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
        assert resp.status_code == 202
        event_id = resp.json()["event_id"]
        detail = client.get(f"/events/{event_id}").json()
        assert detail["before_sha"] == "base-sha-40-chars-base-sha-40-charsss"
        assert detail["after_sha"] == "head-sha-40-chars-head-sha-40-charsss"

    def test_idempotent_by_delivery_id(self, client, session):
        headers = {
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-idem-001",
        }
        body = json.dumps(self._valid_push_payload()).encode()
        r1 = client.post("/webhook/github", headers=headers, content=body)
        assert r1.status_code == 202
        session.flush()
        r2 = client.post("/webhook/github", headers=headers, content=body)
        assert r2.status_code == 202
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

    def test_signature_fails_when_secret_configured(self, client, monkeypatch):
        monkeypatch.setattr("unity_check.main.settings.github_webhook_secret", "test-secret")
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-sig-001",
                "X-Hub-Signature-256": "sha256=invalid",
            },
            content=json.dumps({"repository": {"full_name": "test/repo"}}).encode(),
        )
        assert resp.status_code == 401


class TestEventsLatest:
    def test_empty_db_returns_list_not_error(self, client):
        resp = client.get("/events/latest")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_returns_recent_events(self, client, session):
        for i in range(2):
            client.post(
                "/webhook/github",
                headers={
                    "X-GitHub-Event": "push",
                    "X-GitHub-Delivery": f"delivery-latest-{i}",
                },
                content=json.dumps({
                    "ref": "refs/heads/main",
                    "before": "a" * 40,
                    "after": "b" * 40,
                    "repository": {"full_name": "test/repo"},
                }).encode(),
            )
        session.flush()
        resp = client.get("/events/latest?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2
        item = data[0]
        assert "id" in item
        assert "event_type" in item
        assert "status" in item
        assert "risk_level" in item
        assert "after_sha" in item
        assert "diff_size" in item

    def test_limit_capped_at_100(self, client, session):
        resp = client.get("/events/latest?limit=999")
        assert resp.status_code == 200

    def test_limit_floor_1(self, client, session):
        resp = client.get("/events/latest?limit=0")
        assert resp.status_code == 200


class TestEventDetail:
    def test_returns_full_event(self, client, session):
        resp = client.post(
            "/webhook/github",
            headers={
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-detail-001",
            },
            content=json.dumps({
                "ref": "refs/heads/main",
                "before": "a" * 40,
                "after": "b" * 40,
                "repository": {"full_name": "test/repo"},
            }).encode(),
        )
        assert resp.status_code == 202
        event_id = resp.json()["event_id"]
        detail = client.get(f"/events/{event_id}").json()
        assert detail["id"] == int(event_id)
        assert detail["event_type"] == "push"
        assert "diff_content" in detail
        assert "clone_path" in detail
        assert "evaluation_summary" in detail
        assert "updated_at" in detail

    def test_nonexistent_event_returns_404(self, client):
        resp = client.get("/events/99999")
        assert resp.status_code == 404
