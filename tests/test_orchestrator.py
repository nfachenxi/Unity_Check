"""Tests for orchestrator.py — per-file, per-dimension evaluation pipeline."""

import pytest

from unity_check.models import EvaluationRound, GithubEvent
from unity_check.orchestrator import (
    _event_context,
    _extract_file_diff,
    _persist_evaluation_round,
    _set_safe_defaults,
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
# _extract_file_diff
# ---------------------------------------------------------------------------
class TestExtractFileDiff:
    def test_extracts_single_file(self):
        diff = (
            "diff --git a/A.cs b/A.cs\n"
            "+line1\n"
            "-line2\n"
            "diff --git a/B.cs b/B.cs\n"
            "+other\n"
        )
        result = _extract_file_diff(diff, "A.cs")
        assert "+line1" in result
        assert "+other" not in result

    def test_empty_diff(self):
        assert _extract_file_diff("", "A.cs") == ""

    def test_no_match(self):
        diff = "diff --git a/X.cs b/X.cs\n+stuff\n"
        assert _extract_file_diff(diff, "A.cs") == ""


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
            round_type="functionality_best_practices",
            file_path="A.cs",
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

    def test_writes_row_with_file_path(self, session):
        event = GithubEvent(
            delivery_id="orc-persist-fp",
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
            round_type="functionality_best_practices",
            file_path="Assets/Scripts/Player.cs",
            status="success",
            score=85.0,
        )
        session.commit()
        assert er.file_path == "Assets/Scripts/Player.cs"
        assert er.score == 85.0


# ---------------------------------------------------------------------------
# _set_safe_defaults
# ---------------------------------------------------------------------------
class TestSetSafeDefaults:
    def test_writes_defaults(self):
        event = GithubEvent(
            event_type="push",
            payload={},
            status="running",
        )
        _set_safe_defaults(event, reason="test skip")
        assert event.status == "success"
        assert event.final_risk_level == "unknown"
        assert event.recommendation == "needs_review"
        assert event.overall_score is None
        assert event.executive_summary and "test skip" in event.executive_summary


# ---------------------------------------------------------------------------
# run_evaluation_pipeline
# ---------------------------------------------------------------------------
class TestRunEvaluationPipeline:
    def test_no_cs_files_in_diff(self, session):
        """When diff has no .cs files, pipeline sets safe defaults."""
        event = GithubEvent(
            delivery_id="orc-no-cs",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            diff_content="diff --git a/readme.md b/readme.md\n+hello",
            status="running",
        )
        session.add(event)
        session.commit()

        result = run_evaluation_pipeline(event, session)
        assert result["files_evaluated"] == 0
        assert result["status"] == "success"

        reloaded = session.get(GithubEvent, event.id)
        assert reloaded is not None
        assert reloaded.final_risk_level == "unknown"
        assert "no .cs files" in (reloaded.executive_summary or "")

        # Should have 0 rounds (no rule_check)
        rounds = session.query(EvaluationRound).filter_by(event_id=event.id).all()
        assert len(rounds) == 0

    def test_single_cs_file_produces_two_dimensions(self, session):
        """1 .cs file → 2 rounds: function + security dimensions (no rule_check)."""
        diff = (
            "diff --git a/Assets/Scripts/Player.cs b/Assets/Scripts/Player.cs\n"
            "+void Update() {\n"
            "+    var x = GameObject.Find(\"Player\");\n"
            "+}\n"
        )
        event = GithubEvent(
            delivery_id="orc-1file",
            event_type="push",
            payload={"ref": "refs/heads/main", "commits": [{}]},
            diff_content=diff,
            status="running",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        result = run_evaluation_pipeline(event, session)
        assert result["files_evaluated"] == 1
        assert result["status"] == "success"

        rounds = (
            session.query(EvaluationRound)
            .filter_by(event_id=event_id)
            .order_by(EvaluationRound.round_number, EvaluationRound.id)
            .all()
        )
        dim_a = [r for r in rounds if r.round_type == "functionality_best_practices"]
        dim_b = [r for r in rounds if r.round_type == "security_performance_health"]

        assert len(dim_a) == 1
        assert len(dim_b) == 1
        # Round numbers start at 1
        assert dim_a[0].round_number == 1
        assert dim_b[0].round_number == 1
        assert dim_a[0].file_path == "Assets/Scripts/Player.cs"
        assert dim_b[0].file_path == "Assets/Scripts/Player.cs"

        # Check aggregated event fields
        reloaded = session.get(GithubEvent, event_id)
        assert reloaded is not None
        assert reloaded.overall_score is not None
        assert reloaded.final_risk_level is not None
        assert reloaded.recommendation is not None
        assert reloaded.executive_summary is not None

    def test_aggregation_writes_dimension_scores(self, session):
        """Event gets dimension_a_score and dimension_b_score."""
        diff = "diff --git a/X.cs b/X.cs\n+code\n"
        event = GithubEvent(
            delivery_id="orc-dimscores",
            event_type="push",
            payload={},
            diff_content=diff,
            status="running",
        )
        session.add(event)
        session.commit()

        run_evaluation_pipeline(event, session)
        reloaded = session.get(GithubEvent, event.id)
        assert reloaded is not None
        assert reloaded.dimension_a_score is not None
        assert reloaded.dimension_b_score is not None

    def test_empty_diff_returns_safe_defaults(self, session):
        """No diff content at all → safe defaults."""
        event = GithubEvent(
            delivery_id="orc-empty-diff",
            event_type="push",
            payload={},
            diff_content=None,
            status="running",
        )
        session.add(event)
        session.commit()

        result = run_evaluation_pipeline(event, session)
        assert result["files_evaluated"] == 0
        reloaded = session.get(GithubEvent, event.id)
        assert reloaded is not None
        assert reloaded.final_risk_level == "unknown"
