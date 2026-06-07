"""Per-file evaluation orchestrator.

For each .cs file in the diff:
1. Aggregate Roslyn RuleResult → 1 rule_check EvaluationRound (round_number=0)
2. For each file (round_number = file_index, 1..N):
   a. Evaluate dimension A: functionality_best_practices → EvaluationRound
   b. Evaluate dimension B: security_performance_health → EvaluationRound
3. Programmatically aggregate all dimension results → update GithubEvent
4. Trigger notification
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from unity_check.llm import evaluate_file_dimension
from unity_check.models import EvaluationRound, GithubEvent, RuleResult
from unity_check.notification_service import build_and_persist_notifications
from unity_check.rule_service import extract_cs_files_from_diff

logger = logging.getLogger(__name__)

DIMENSIONS = ["functionality_best_practices", "security_performance_health"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_evaluation_pipeline(event: GithubEvent, db: Session) -> dict[str, Any]:
    """Execute per-file, per-dimension evaluation for *event*.

    Side-effects
    ------------
    * Inserts ``1 + N*2`` ``EvaluationRound`` rows (1 rule_check + N files × 2 dimensions).
    * Mutates *event* in-place: ``overall_score``, ``final_risk_level``,
      ``recommendation``, ``executive_summary``, ``dimension_a_score``,
      ``dimension_b_score``, ``dimension_a_summary``, ``dimension_b_summary``,
      ``status``.

    Returns
    -------
    dict
        Summary of the pipeline run.
    """
    event_id = int(event.id)
    diff = (event.diff_content or "").strip()
    event_summary = _event_context(event)

    # ---- Step 1: Rule results summary (programmatic, no LLM) -----------------
    r1_start = datetime.now(timezone.utc)
    r1_summary = _build_rule_results_summary(event_id, db)
    r1_duration = int((datetime.now(timezone.utc) - r1_start).total_seconds() * 1000)
    _persist_evaluation_round(
        db,
        event_id=event_id,
        round_number=0,
        round_type="rule_check",
        status="success",
        input_summary={"diff_size": len(diff), "event_summary": event_summary},
        output_data=r1_summary,
        tokens_used=0,
        duration_ms=r1_duration,
    )
    logger.info("Rule check done for event_id=%s: %d violations", event_id, r1_summary.get("total", 0))

    # ---- Step 2: Extract .cs files from diff --------------------------------
    cs_files = extract_cs_files_from_diff(diff) if diff else []
    if not cs_files:
        logger.info("No .cs files in diff for event_id=%s — using safe defaults", event_id)
        _set_safe_defaults(event, reason="no .cs files in diff")
        build_and_persist_notifications(event, db)
        return {
            "status": event.status,
            "files_evaluated": 0,
            "overall_score": event.overall_score,
            "risk_level": event.final_risk_level,
            "recommendation": event.recommendation,
        }

    # ---- Step 3: Per-file, per-dimension LLM evaluation ---------------------
    all_dim_scores: dict[str, list[float]] = {"functionality_best_practices": [], "security_performance_health": []}
    all_dim_summaries: dict[str, list[str]] = {"functionality_best_practices": [], "security_performance_health": []}
    all_findings: list[dict[str, Any]] = []
    total_files_evaluated = 0
    rounds_completed = 0

    per_file_rules = r1_summary.get("by_file", {})

    for file_idx, file_path in enumerate(cs_files, start=1):
        # Extract file-specific diff
        file_diff = _extract_file_diff(diff, file_path)
        # Get file-specific rule results
        file_rules = per_file_rules.get(file_path, [])

        for dim in DIMENSIONS:
            start = datetime.now(timezone.utc)
            try:
                result = evaluate_file_dimension(
                    file_path=file_path,
                    file_diff=file_diff,
                    file_rule_results=file_rules,
                    event_summary=event_summary,
                    dimension=dim,
                )
                dur = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

                if result.get("error"):
                    _persist_evaluation_round(
                        db, event_id=event_id, round_number=file_idx,
                        round_type=dim, file_path=file_path, status="failed",
                        input_summary={"file_path": file_path, "dimension": dim},
                        output_data=None, model_name=result.get("model_name", ""),
                        tokens_used=result.get("tokens_used", 0), duration_ms=dur,
                        error_message=result["error"],
                    )
                    all_dim_scores[dim].append(0.0)
                else:
                    score = result.get("score", 0)
                    summary = result.get("summary", "")
                    findings = result.get("findings", [])

                    _persist_evaluation_round(
                        db, event_id=event_id, round_number=file_idx,
                        round_type=dim, file_path=file_path, status="success",
                        input_summary={"file_path": file_path, "dimension": dim},
                        output_data={"score": score, "summary": summary, "findings": findings},
                        score=score,
                        model_name=result.get("model_name", ""),
                        tokens_used=result.get("tokens_used", 0), duration_ms=dur,
                    )
                    all_dim_scores[dim].append(score)
                    all_dim_summaries[dim].append(summary)
                    all_findings.extend(findings)
                    rounds_completed += 1

            except Exception as exc:
                dur = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
                _persist_evaluation_round(
                    db, event_id=event_id, round_number=file_idx,
                    round_type=dim, file_path=file_path, status="failed",
                    input_summary={"file_path": file_path, "dimension": dim},
                    output_data=None, tokens_used=0, duration_ms=dur,
                    error_message=str(exc),
                )
                logger.exception("Dimension %s failed for %s event_id=%s", dim, file_path, event_id)

        total_files_evaluated += 1

    # ---- Step 4: Programmatic aggregation -----------------------------------
    _aggregate_and_update_event(event, all_dim_scores, all_dim_summaries, all_findings, cs_files)

    # ---- Step 5: Notification -----------------------------------------------
    build_and_persist_notifications(event, db)

    return {
        "status": event.status,
        "files_evaluated": total_files_evaluated,
        "rounds_completed": rounds_completed,
        "overall_score": event.overall_score,
        "risk_level": event.final_risk_level,
        "recommendation": event.recommendation,
    }


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------


def _aggregate_and_update_event(
    event: GithubEvent,
    dim_scores: dict[str, list[float]],
    dim_summaries: dict[str, list[str]],
    all_findings: list[dict[str, Any]],
    cs_files: list[str],
) -> None:
    """Compute aggregated scores/risk and write to event."""
    dim_a = dim_scores["functionality_best_practices"]
    dim_b = dim_scores["security_performance_health"]

    # Dimension scores: average per file
    dim_a_avg = round(sum(dim_a) / len(dim_a), 1) if dim_a else None
    dim_b_avg = round(sum(dim_b) / len(dim_b), 1) if dim_b else None

    # Overall score: average of all dimension scores (weighted equally across files)
    all_scores = dim_a + dim_b
    overall_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else None

    # Risk level: highest severity across all findings
    severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    max_sev_score = 0
    max_sev = "low"
    for f in all_findings:
        sev = str(f.get("severity", "low")).lower()
        s = severity_order.get(sev, 0)
        if s > max_sev_score:
            max_sev_score = s
            max_sev = sev

    # Recommendation
    if overall_score is not None:
        if max_sev == "critical" or overall_score < 50:
            recommendation = "blocked"
        elif overall_score >= 80 and max_sev in ("low", "medium"):
            recommendation = "merge_ready"
        else:
            recommendation = "needs_review"
    else:
        recommendation = "needs_review"

    # Top issues (max 10, ordered by severity)
    top_issues = sorted(all_findings, key=lambda f: severity_order.get(str(f.get("severity", "low")).lower(), 0), reverse=True)[:10]

    # Executive summary
    dim_a_sum = f"功能/最佳实践: {dim_a_avg}/100" if dim_a_avg is not None else "功能/最佳实践: N/A"
    dim_b_sum = f"安全/性能/健康度: {dim_b_avg}/100" if dim_b_avg is not None else "安全/性能/健康度: N/A"
    top_titles = "; ".join(f["title"] for f in top_issues[:3]) if top_issues else "无严重问题"

    executive_summary = (
        f"评估了 {len(cs_files)} 个文件。综合评分: {overall_score}/100。"
        f"风险等级: {max_sev}。{dim_a_sum}。{dim_b_sum}。"
        f"主要问题: {top_titles}"
    )

    event.overall_score = overall_score
    event.final_risk_level = max_sev
    event.recommendation = recommendation
    event.executive_summary = executive_summary
    event.dimension_a_score = dim_a_avg
    event.dimension_b_score = dim_b_avg
    event.dimension_a_summary = "; ".join(dim_summaries["functionality_best_practices"][:3]) if dim_summaries["functionality_best_practices"] else None
    event.dimension_b_summary = "; ".join(dim_summaries["security_performance_health"][:3]) if dim_summaries["security_performance_health"] else None
    event.status = "success"

    logger.info(
        "Aggregation done for event_id=%s: score=%s risk=%s recommendation=%s",
        event.id, overall_score, max_sev, recommendation,
    )


def _set_safe_defaults(event: GithubEvent, reason: str) -> None:
    """Set safe fallback values when evaluation cannot proceed."""
    event.overall_score = None
    event.final_risk_level = "unknown"
    event.recommendation = "needs_review"
    event.executive_summary = f"跳过评估: {reason}"
    event.dimension_a_score = None
    event.dimension_b_score = None
    event.dimension_a_summary = None
    event.dimension_b_summary = None
    event.status = "success"


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
    """Aggregate ``RuleResult`` rows for *event_id* into a summary dict.

    Includes per-file breakdown for filtering rule results to individual files.
    """
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
            "by_file": {},
        }

    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    file_counts: dict[str, int] = {}
    by_file: dict[str, list[dict[str, Any]]] = {}

    for r in rows:
        sev = r.severity.lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        cat = (r.category or "uncategorized").lower()
        category_counts[cat] = category_counts.get(cat, 0) + 1
        key = f"{r.rule_id}: {r.rule_name}"
        rule_counts[key] = rule_counts.get(key, 0) + 1
        file_counts[r.file_path] = file_counts.get(r.file_path, 0) + 1

        # Per-file detail for LLM context
        if r.file_path not in by_file:
            by_file[r.file_path] = []
        by_file[r.file_path].append({
            "rule_id": r.rule_id,
            "rule_name": r.rule_name,
            "severity": r.severity,
            "category": r.category,
            "message": r.message,
            "line_number": r.line_number,
        })

    top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "total": len(rows),
        "by_severity": severity_counts,
        "by_category": category_counts,
        "top_rules": [{"rule": k, "count": v} for k, v in top_rules],
        "top_files": [{"file": k, "count": v} for k, v in top_files],
        "by_file": by_file,
    }


def _extract_file_diff(full_diff: str, file_path: str) -> str:
    """Extract diff blocks relevant to *file_path* from a unified diff.

    Parses ``diff --git a/<path> b/<path>`` headers and keeps blocks
    matching the given file path.
    """
    if not full_diff:
        return ""

    lines = full_diff.split("\n")
    result: list[str] = []
    in_block = False
    current_path = ""

    for line in lines:
        if line.startswith("diff --git "):
            # Extract b/ path
            parts = line.split(" ")
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    b_path = b_path[2:]
                current_path = b_path
                in_block = (current_path == file_path)
            else:
                in_block = False
        if in_block:
            result.append(line)

    return "\n".join(result)


def _persist_evaluation_round(
    db: Session,
    *,
    event_id: int,
    round_number: int,
    round_type: str,
    status: str,
    file_path: str | None = None,
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
        file_path=file_path,
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
