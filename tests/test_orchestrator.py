"""Tests for orchestrator.py — pipeline execution, persistence, degradation."""

import pytest

from unity_check.models import EvaluationRound, GithubEvent, RuleResult
from unity_check.orchestrator import (
    _build_rule_results_summary,
    _event_context,
    _persist_evaluation_round,
    run_evaluation_pipeline,
)


# ---------------------------------------------------------------------------
# _event_context
# ---------------------------------------------------------------------------
class TestEventContext:
    def test_push_context(self):
        event = GithubEvent(
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}, {}]},
        )
        ctx = _event_context(event)
        assert "push to refs/heads/main" in ctx
        assert "commits=2" in ctx

    def test_pr_context(self):
        event = GithubEvent(
            event_type="pull_request",
            action="opened",
            payload={"pull_request": {"number": 42, "title": "Fix bug"}},
        )
        ctx = _event_context(event)
        assert "pull_request #42" in ctx
        assert "Fix bug" in ctx

    def test_unknown_event_type(self):
        event = GithubEvent(event_type="issues", action="opened", payload={})
        ctx = _event_context(event)
        assert "issues" in ctx


# ---------------------------------------------------------------------------
# _build_rule_results_summary
# ---------------------------------------------------------------------------
class TestBuildRuleResultsSummary:
    def test_empty_when_no_rules(self, session):
        event = GithubEvent(
            delivery_id="orc-summary-empty",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()

        summary = _build_rule_results_summary(event.id, session)
        assert summary["total"] == 0
        assert summary["by_severity"] == {}
        assert summary["by_category"] == {}
        assert summary["top_rules"] == []
        assert summary["top_files"] == []

    def test_aggregates_rules(self, session):
        event = GithubEvent(
            delivery_id="orc-summary-data",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        session.add_all([
            RuleResult(
                event_id=event_id, rule_id="R1", rule_name="Rule One",
                file_path="A.cs", severity="Warning", category="Perf",
                message="m", scan_type="incremental",
            ),
            RuleResult(
                event_id=event_id, rule_id="R1", rule_name="Rule One",
                file_path="B.cs", severity="Warning", category="Perf",
                message="m", scan_type="incremental",
            ),
            RuleResult(
                event_id=event_id, rule_id="R2", rule_name="Rule Two",
                file_path="A.cs", severity="Error", category="Naming",
                message="m", scan_type="incremental",
            ),
        ])
        session.commit()

        summary = _build_rule_results_summary(event_id, session)
        assert summary["total"] == 3
        assert summary["by_severity"] == {"warning": 2, "error": 1}
        assert "perf" in summary["by_category"]
        assert "naming" in summary["by_category"]
        assert len(summary["top_rules"]) == 2
        assert len(summary["top_files"]) >= 1


# ---------------------------------------------------------------------------
# _persist_evaluation_round
# ---------------------------------------------------------------------------
class TestPersistEvaluationRound:
    def test_writes_row(self, session):
        event = GithubEvent(
            delivery_id="orc-persist",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()

        er = _persist_evaluation_round(
            db=session,
            event_id=event.id,
            round_number=1,
            round_type="rule_check",
            status="success",
            input_summary={"k": "v"},
            output_data={"total": 5},
            tokens_used=0,
            duration_ms=50,
        )
        assert er.id is not None
        assert er.round_number == 1
        assert er.status == "success"

        rows = session.query(EvaluationRound).filter_by(event_id=event.id).all()
        assert len(rows) == 1
        assert rows[0].input_summary == {"k": "v"}
        assert rows[0].output_data == {"total": 5}


# ---------------------------------------------------------------------------
# run_evaluation_pipeline (uses conftest.py _mock_llm fixture)
# ---------------------------------------------------------------------------
class TestRunEvaluationPipeline:
    def test_all_three_rounds_succeed(self, session):
        """Full pipeline → 3 evaluation_rounds rows, event fields updated."""
        event = GithubEvent(
            delivery_id="orc-full-success",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            diff_content="diff --git a/A.cs b/A.cs\n+void Update() {}",
            status="running",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        result = run_evaluation_pipeline(event, session)
        assert result["status"] == "success"
        assert result["rounds_completed"] == 3
        assert result["risk_level"] == "low"

        rounds = (
            session.query(EvaluationRound)
            .filter_by(event_id=event_id)
            .order_by(EvaluationRound.round_number)
            .all()
        )
        assert len(rounds) == 3
        assert rounds[0].round_type == "rule_check"
        assert rounds[0].status == "success"
        assert rounds[1].round_type == "semantic_review"
        assert rounds[1].status == "success"
        assert rounds[2].round_type == "synthesis"
        assert rounds[2].status == "success"

        # Verify GithubEvent fields.
        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.overall_score == 85.0
        assert reloaded.final_risk_level == "low"
        assert reloaded.recommendation == "merge_ready"
        # Backward compatible.
        assert reloaded.risk_level == "low"
        assert reloaded.evaluation_summary == "Mocked executive summary."

    def test_r2_fails_r3_continues(self, session, monkeypatch):
        """R2 returns error → R3 still executes with r2_findings=None."""
        # Override the autouse mock: make semantic_review return an error.
        def _failing_r2(*args, **kwargs):
            return {
                "findings": [],
                "error": "Simulated R2 failure",
                "tokens_used": 0,
                "duration_ms": 10,
                "model_name": "mock",
            }

        monkeypatch.setattr("unity_check.orchestrator.semantic_review", _failing_r2)

        event = GithubEvent(
            delivery_id="orc-r2-fail",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            diff_content="diff --git a/A.cs b/A.cs",
            status="running",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        result = run_evaluation_pipeline(event, session)
        # Pipeline should still produce a result; R3 runs regardless.
        rounds = (
            session.query(EvaluationRound)
            .filter_by(event_id=event_id)
            .order_by(EvaluationRound.round_number)
            .all()
        )
        assert len(rounds) == 3
        r2 = [r for r in rounds if r.round_number == 2][0]
        assert r2.status == "failed"
        r3 = [r for r in rounds if r.round_number == 3][0]
        assert r3.status == "success"  # R3 mock still succeeds
        assert r3.input_summary["r2_success"] is False

    def test_r2_exception_r3_continues(self, session, monkeypatch):
        """R2 raises exception → caught, persisted as failed, R3 continues."""
        def _crashing_r2(*args, **kwargs):
            raise RuntimeError("Boom!")

        monkeypatch.setattr("unity_check.orchestrator.semantic_review", _crashing_r2)

        event = GithubEvent(
            delivery_id="orc-r2-crash",
            event_type="push",
            payload={},
            status="running",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        result = run_evaluation_pipeline(event, session)
        rounds = (
            session.query(EvaluationRound)
            .filter_by(event_id=event_id)
            .order_by(EvaluationRound.round_number)
            .all()
        )
        assert len(rounds) == 3
        r2 = [r for r in rounds if r.round_number == 2][0]
        assert r2.status == "failed"
        assert "Boom" in (r2.error_message or "")
        r3 = [r for r in rounds if r.round_number == 3][0]
        assert r3.status == "success"

    def test_backward_compatible_fields(self, session):
        """risk_level and evaluation_summary are set on the event."""
        event = GithubEvent(
            delivery_id="orc-compat",
            event_type="push",
            payload={},
            status="running",
        )
        session.add(event)
        session.commit()

        run_evaluation_pipeline(event, session)
        reloaded = session.get(GithubEvent, event.id)
        assert reloaded is not None
        assert reloaded.risk_level == "low"
        assert reloaded.evaluation_summary == "Mocked executive summary."

    def test_r3_failure_still_persists_r1_r2(self, session, monkeypatch):
        """When R3 fails, R1 and R2 rows are still persisted, event gets safe defaults."""

        def _crashing_r3(*args, **kwargs):
            raise RuntimeError("R3 crash")

        monkeypatch.setattr("unity_check.orchestrator.synthesis_summary", _crashing_r3)

        event = GithubEvent(
            delivery_id="orc-r3-crash",
            event_type="push",
            payload={},
            status="running",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        result = run_evaluation_pipeline(event, session)
        rounds = (
            session.query(EvaluationRound)
            .filter_by(event_id=event_id)
            .order_by(EvaluationRound.round_number)
            .all()
        )
        assert len(rounds) == 3
        assert rounds[0].status == "success"  # R1 ok
        assert rounds[1].status == "success"  # R2 ok (mock)
        assert rounds[2].status == "failed"   # R3 crashed

        reloaded = session.get(GithubEvent, event.id)
        assert reloaded is not None
        assert reloaded.status == "failed"
        assert reloaded.recommendation == "needs_review"
