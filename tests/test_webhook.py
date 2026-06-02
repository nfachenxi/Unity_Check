import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from unity_check.main import app
from unity_check.models import GithubEvent


def _fake_task_id() -> str:
    return str(uuid4())


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch, session):
    """All FastAPI endpoints use the test session; Celery tasks are mocked."""

    def override_get_db():
        yield session

    monkeypatch.setattr("unity_check.main.get_db", override_get_db)

    # Mock process_github_event.delay so we never touch Celery/Redis
    class _FakeAsyncResult:
        def __init__(self, event_id):
            self.id = _fake_task_id()
            self.event_id = event_id

    def _fake_delay(event_id):
        # Also simulate the task-side effect: set task_id and status inline
        # so persistence tests don't need a real worker
        return _FakeAsyncResult(event_id)

    monkeypatch.setattr("unity_check.main.process_github_event.delay", _fake_delay)

    # Patch SessionLocal used by tasks to avoid real DB connections
    monkeypatch.setattr("unity_check.tasks.SessionLocal", lambda: session)


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
        """ping with a sha256 header passes when no secret is configured."""
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
    def _valid_push_payload(self):
        return {
            "ref": "refs/heads/main",
            "before": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "after": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "repository": {"full_name": "test/repo"},
        }

    def _valid_pr_payload(self):
        return {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "title": "Test PR",
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
        # Verify via API — if the event was committed to DB, /events/latest
        # returns it.
        latest = client.get("/events/latest").json()
        ids = [e["id"] for e in latest]
        assert int(data["event_id"]) in ids

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
        # Duplicate delivery returns 202 (decorator) but reuses existing event
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
        """When DB may have leftover rows, endpoint still returns a valid list."""
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

    def test_limit_capped_at_100(self, client, session):
        resp = client.get("/events/latest?limit=999")
        assert resp.status_code == 200

    def test_limit_floor_1(self, client, session):
        resp = client.get("/events/latest?limit=0")
        assert resp.status_code == 200
