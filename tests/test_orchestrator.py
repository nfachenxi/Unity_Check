"""Tests for orchestrator.py — per-file, per-dimension evaluation pipeline."""

import pytest

from unity_check.models import EvaluationRound, GithubEvent, RuleResult
from unity_check.orchestrator import (
    _build_rule_results_summary,
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
        assert summary["by_file"] == {}

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
        # Per-file breakdown
        assert "A.cs" in summary["by_file"]
        assert len(summary["by_file"]["A.cs"]) == 2
        assert "B.cs" in summary["by_file"]
        assert len(summary["by_file"]["B.cs"]) == 1


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
            round_number=0,
            round_type="rule_check",
            status="success",
            input_summary={"k": "v"},
            output_data={"total": 5},
            tokens_used=0,
            duration_ms=50,
        )
        assert er.id is not None
        assert er.round_number == 0
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

        # Should have 1 rule_check round (round 0, with total=0)
        rounds = session.query(EvaluationRound).filter_by(event_id=event.id).all()
        assert len(rounds) == 1
        assert rounds[0].round_type == "rule_check"
        assert rounds[0].round_number == 0

    def test_single_cs_file_produces_two_dimensions(self, session):
        """1 .cs file → 3 rounds: 1 rule_check + 2 dimensions."""
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
        rule_check = [r for r in rounds if r.round_type == "rule_check"]
        dim_a = [r for r in rounds if r.round_type == "functionality_best_practices"]
        dim_b = [r for r in rounds if r.round_type == "security_performance_health"]

        assert len(rule_check) == 1
        assert rule_check[0].round_number == 0
        assert len(dim_a) == 1
        assert len(dim_b) == 1
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
