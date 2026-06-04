"""Multi-round evaluation orchestrator.

Orchestrates three evaluation rounds for a single GitHub event:

1. Round 1 (rule_check)   — summarises ``RuleResult`` rows from Roslyn.
2. Round 2 (semantic_review) — LLM semantic deep-dive on the diff + R1 results.
3. Round 3 (synthesis)       — LLM final assessment: score, risk, recommendation.

Each round writes an independent ``EvaluationRound`` row so the pipeline
can be inspected and debugged step-by-step.  Round 2 failures are tolerated;
Round 3 always runs, with or without R2 findings.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from unity_check.llm import semantic_review, synthesis_summary
from unity_check.models import EvaluationRound, GithubEvent, RuleResult
from unity_check.notification_service import build_and_persist_notifications

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_evaluation_pipeline(event: GithubEvent, db: Session) -> dict[str, Any]:
    """Execute the full three-round evaluation pipeline for *event*.

    Side-effects
    ------------
    * Inserts 3 ``EvaluationRound`` rows (one per round).
    * Mutates *event* in-place: ``overall_score``, ``final_risk_level``,
      ``recommendation``, ``executive_summary``, ``risk_level``,
      ``evaluation_summary``, ``status``.

    Returns
    -------
    dict
        Summary of the pipeline run suitable for logging / task response.
    """
    event_id = int(event.id)
    diff = (event.diff_content or "").strip()
    event_summary = _event_context(event)

    # ---- Round 1: rule check summary ---------------------------------------
    r1_start = datetime.now(timezone.utc)
    r1_summary = _build_rule_results_summary(event_id, db)
    r1_duration = int((datetime.now(timezone.utc) - r1_start).total_seconds() * 1000)
    _persist_evaluation_round(
        db,
        event_id=event_id,
        round_number=1,
        round_type="rule_check",
        status="success",
        input_summary={"diff_size": len(diff), "event_summary": event_summary},
        output_data=r1_summary,
        tokens_used=0,
        duration_ms=r1_duration,
    )
    logger.info("R1 rule_check done for event_id=%s: %d violations", event_id, r1_summary.get("total", 0))

    # ---- Round 2: semantic review ------------------------------------------
    r2_findings: list[dict[str, Any]] | None = None
    r2_success = False
    r2_error: str | None = None

    r2_start = datetime.now(timezone.utc)
    try:
        r2_result = semantic_review(
            diff_content=diff,
            rule_results_summary=r1_summary,
            event_summary=event_summary,
        )
        r2_duration = int((datetime.now(timezone.utc) - r2_start).total_seconds() * 1000)

        if r2_result.get("error"):
            r2_error = r2_result["error"]
            _persist_evaluation_round(
                db,
                event_id=event_id,
                round_number=2,
                round_type="semantic_review",
                status="failed",
                input_summary=_build_r2_input(diff, r1_summary, event_summary),
                output_data=None,
                model_name=r2_result.get("model_name", ""),
                tokens_used=r2_result.get("tokens_used", 0),
                duration_ms=r2_duration,
                error_message=r2_error,
            )
        else:
            r2_findings = r2_result.get("findings", [])
            r2_success = True
            _persist_evaluation_round(
                db,
                event_id=event_id,
                round_number=2,
                round_type="semantic_review",
                status="success",
                input_summary=_build_r2_input(diff, r1_summary, event_summary),
                output_data={"findings": r2_findings},
                model_name=r2_result.get("model_name", ""),
                tokens_used=r2_result.get("tokens_used", 0),
                duration_ms=r2_duration,
            )
        logger.info(
            "R2 semantic_review done for event_id=%s: success=%s findings=%d",
            event_id, r2_success, len(r2_findings or []),
        )
    except Exception as exc:
        r2_duration = int((datetime.now(timezone.utc) - r2_start).total_seconds() * 1000)
        r2_error = str(exc)
        _persist_evaluation_round(
            db,
            event_id=event_id,
            round_number=2,
            round_type="semantic_review",
            status="failed",
            input_summary=_build_r2_input(diff, r1_summary, event_summary),
            output_data=None,
            tokens_used=0,
            duration_ms=r2_duration,
            error_message=r2_error,
        )
        logger.exception("R2 semantic_review exception for event_id=%s", event_id)

    # ---- Round 3: synthesis -------------------------------------------------
    r3_start = datetime.now(timezone.utc)
    try:
        r3_result = synthesis_summary(
            diff_content=diff,
            rule_results_summary=r1_summary,
            r2_findings=r2_findings,
            event_summary=event_summary,
        )
        r3_duration = int((datetime.now(timezone.utc) - r3_start).total_seconds() * 1000)

        if r3_result.get("error"):
            _persist_evaluation_round(
                db,
                event_id=event_id,
                round_number=3,
                round_type="synthesis",
                status="failed",
                input_summary=_build_r3_input(diff, r1_summary, r2_findings, r2_error, event_summary),
                output_data=None,
                model_name=r3_result.get("model_name", ""),
                tokens_used=r3_result.get("tokens_used", 0),
                duration_ms=r3_duration,
                error_message=r3_result["error"],
            )
            # Fill safe defaults on the event.
            event.overall_score = None
            event.final_risk_level = "unknown"
            event.recommendation = "needs_review"
            event.executive_summary = f"Round 3 failed: {r3_result['error']}"
            event.risk_level = "unknown"
            event.evaluation_summary = event.executive_summary
            event.status = "failed"
        else:
            _persist_evaluation_round(
                db,
                event_id=event_id,
                round_number=3,
                round_type="synthesis",
                status="success",
                input_summary=_build_r3_input(diff, r1_summary, r2_findings, r2_error, event_summary),
                output_data={
                    "overall_score": r3_result["overall_score"],
                    "risk_level": r3_result["risk_level"],
                    "executive_summary": r3_result["executive_summary"],
                    "top_issues": r3_result.get("top_issues", []),
                    "recommendation": r3_result["recommendation"],
                    "action_items": r3_result.get("action_items", []),
                },
                score=r3_result["overall_score"],
                model_name=r3_result.get("model_name", ""),
                tokens_used=r3_result.get("tokens_used", 0),
                duration_ms=r3_duration,
            )
            # Update event with final assessment.
            event.overall_score = r3_result["overall_score"]
            event.final_risk_level = r3_result["risk_level"]
            event.recommendation = r3_result["recommendation"]
            event.executive_summary = r3_result["executive_summary"]
            # Backward-compatible snapshot.
            event.risk_level = r3_result["risk_level"]
            event.evaluation_summary = r3_result["executive_summary"]
            event.status = "success"

        logger.info(
            "R3 synthesis done for event_id=%s: score=%s risk=%s recommendation=%s",
            event_id,
            r3_result.get("overall_score"),
            r3_result.get("risk_level"),
            r3_result.get("recommendation"),
        )
    except Exception as exc:
        r3_duration = int((datetime.now(timezone.utc) - r3_start).total_seconds() * 1000)
        _persist_evaluation_round(
            db,
            event_id=event_id,
            round_number=3,
            round_type="synthesis",
            status="failed",
            input_summary=_build_r3_input(diff, r1_summary, r2_findings, r2_error, event_summary),
            output_data=None,
            tokens_used=0,
            duration_ms=r3_duration,
            error_message=str(exc),
        )
        event.overall_score = None
        event.final_risk_level = "unknown"
        event.recommendation = "needs_review"
        event.executive_summary = f"Round 3 exception: {exc}"
        event.risk_level = "unknown"
        event.evaluation_summary = event.executive_summary
        event.status = "failed"
        logger.exception("R3 synthesis exception for event_id=%s", event_id)

    # ---- Post-pipeline: notification -------------------------------------
    build_and_persist_notifications(event, db)

    # Build result summary.
    rounds_completed = 0
    if r1_summary:
        rounds_completed += 1
    if r2_success:
        rounds_completed += 1
    if event.status == "success":
        rounds_completed = 3

    return {
        "status": event.status,
        "rounds_completed": rounds_completed,
        "overall_score": event.overall_score,
        "risk_level": event.final_risk_level,
        "recommendation": event.recommendation,
        "executive_summary": event.executive_summary,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event_context(event: GithubEvent) -> str:
    """Build a one-line context string for the event."""
    payload = event.payload or {}
    if event.event_type == "push":
        commit_count = len(payload.get("commits", []))
        ref = payload.get("ref", "unknown")
        return f"push to {ref}, commits={commit_count}"
    if event.event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        pr_number = pr.get("number", payload.get("number", "unknown"))
        title = pr.get("title", "")
        return f"pull_request #{pr_number}, action={event.action or 'unknown'}, title={title}"
    return f"event={event.event_type}, action={event.action or 'none'}"


def _build_rule_results_summary(event_id: int, db: Session) -> dict[str, Any]:
    """Aggregate ``RuleResult`` rows for *event_id* into a summary dict."""
    rows = db.scalars(
        select(RuleResult).where(RuleResult.event_id == event_id)
    ).all()

    if not rows:
        return {
            "total": 0,
            "by_severity": {},
            "by_category": {},
            "top_rules": [],
            "top_files": [],
        }

    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}

    for r in rows:
        sev = r.severity.lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        cat = (r.category or "uncategorized").lower()
        category_counts[cat] = category_counts.get(cat, 0) + 1
        key = f"{r.rule_id}: {r.rule_name}"
        rule_counts[key] = rule_counts.get(key, 0) + 1
        file_counts[r.file_path] = file_counts.get(r.file_path, 0) + 1

    top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total": len(rows),
        "by_severity": severity_counts,
        "by_category": category_counts,
        "top_rules": [{"rule": k, "count": v} for k, v in top_rules],
        "top_files": [{"file": k, "count": v} for k, v in top_files],
    }


def _persist_evaluation_round(
    db: Session,
    *,
    event_id: int,
    round_number: int,
    round_type: str,
    status: str,
    input_summary: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    score: float | None = None,
    model_name: str | None = None,
    tokens_used: int | None = None,
    duration_ms: int | None = None,
    error_message: str | None = None,
) -> EvaluationRound:
    """Insert (and flush) a single ``EvaluationRound`` row."""
    now = datetime.now(timezone.utc)
    er = EvaluationRound(
        event_id=event_id,
        round_number=round_number,
        round_type=round_type,
        status=status,
        input_summary=input_summary,
        output_data=output_data,
        score=score,
        model_name=model_name,
        tokens_used=tokens_used,
        duration_ms=duration_ms,
        error_message=error_message,
        started_at=now,
        completed_at=now if status in ("success", "failed") else None,
    )
    db.add(er)
    db.flush()
    return er


def _build_r2_input(
    diff: str, rule_summary: dict[str, Any], event_summary: str
) -> dict[str, Any]:
    return {
        "diff_size": len(diff),
        "rule_summary": rule_summary,
        "event_summary": event_summary,
    }


def _build_r3_input(
    diff: str,
    rule_summary: dict[str, Any],
    r2_findings: list[dict[str, Any]] | None,
    r2_error: str | None,
    event_summary: str,
) -> dict[str, Any]:
    return {
        "diff_size": len(diff),
        "rule_summary": rule_summary,
        "r2_findings_count": len(r2_findings) if r2_findings else 0,
        "r2_success": r2_findings is not None,
        "r2_error": r2_error,
        "event_summary": event_summary,
    }
