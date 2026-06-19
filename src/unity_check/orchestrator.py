"""Per-file evaluation orchestrator.

For each .cs file in the diff:
1. Extract .cs files from diff
2. Per-file, per-dimension LLM evaluation (2 dimensions per file)
3. Programmatically aggregate → update GithubEvent
"""

from __future__ import annotations

import collections.abc
import concurrent.futures
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.db import SessionLocal
from unity_check.llm import evaluate_file_dimension
from unity_check.models import EvaluationRound, GithubEvent
from unity_check.rule_service import extract_cs_files_from_diff

logger = logging.getLogger(__name__)

DIMENSIONS = ["functionality_best_practices", "security_performance_health"]

# ---------------------------------------------------------------------------
# Thread worker — evaluates one file (both dimensions)
# ---------------------------------------------------------------------------


def _evaluate_single_file(
    file_idx: int,
    file_path: str,
    file_diff: str,
    event_summary: str,
    event_id: int,
) -> dict[str, Any]:
    """Evaluate both dimensions for a single file.

    Intended to run inside a ``ThreadPoolExecutor`` worker thread.
    Creates an independent DB session, persists results, and returns
    structured data for the caller to aggregate.

    Returns
    -------
    dict
        ``file_path``, ``file_idx``, ``dim_a``, ``dim_b``
        On failure ``error`` is also set.
    """
    session: Session | None = None
    dim_a_result: dict[str, Any] = {}
    dim_b_result: dict[str, Any] = {}
    error: str | None = None
    file_error_message: str | None = None

    try:
        session = SessionLocal()

        # Dimension A — functionality_best_practices
        dim_a_start = datetime.now(timezone.utc)
        dim_a_result = evaluate_file_dimension(
            file_path=file_path,
            file_diff=file_diff,
            event_summary=event_summary,
            dimension="functionality_best_practices",
        )
        dim_a_dur = int((datetime.now(timezone.utc) - dim_a_start).total_seconds() * 1000)
        _persist_dimension_result(
            session, event_id, file_idx, file_path,
            "functionality_best_practices", dim_a_result, dim_a_dur,
        )

        # Dimension B — security_performance_health
        dim_b_start = datetime.now(timezone.utc)
        dim_b_result = evaluate_file_dimension(
            file_path=file_path,
            file_diff=file_diff,
            event_summary=event_summary,
            dimension="security_performance_health",
        )
        dim_b_dur = int((datetime.now(timezone.utc) - dim_b_start).total_seconds() * 1000)
        _persist_dimension_result(
            session, event_id, file_idx, file_path,
            "security_performance_health", dim_b_result, dim_b_dur,
        )

        session.commit()

    except Exception as exc:
        logger.exception("Worker evaluation failed for %s event_id=%s", file_path, event_id)
        error = "worker_exception"
        file_error_message = str(exc)
        if session is not None:
            try:
                session.rollback()
            except Exception:
                pass
    finally:
        if session is not None:
            session.close()

    return {
        "file_path": file_path,
        "file_idx": file_idx,
        "dim_a": dim_a_result,
        "dim_b": dim_b_result,
        "error": error,
        "error_message": file_error_message,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_evaluation_pipeline(
    event: GithubEvent, db: Session,
    progress_callback: collections.abc.Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Execute per-file, per-dimension evaluation for *event*.

    Side-effects
    ------------
    * Inserts ``N*2`` ``EvaluationRound`` rows (N files × 2 dimensions).
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

    # ---- Step 1: Extract .cs files from diff --------------------------------
    cs_files = extract_cs_files_from_diff(diff) if diff else []
    if not cs_files:
        logger.info("No .cs files in diff for event_id=%s — using safe defaults", event_id)
        _set_safe_defaults(event, reason="no .cs files in diff")
        return {
            "status": event.status,
            "files_evaluated": 0,
            "overall_score": event.overall_score,
            "risk_level": event.final_risk_level,
            "recommendation": event.recommendation,
        }

    # ---- Step 2: Parallel file-level evaluation ----------------------------
    total_files = len(cs_files)
    completed_count = 0
    completed_lock = threading.Lock()
    all_results: list[dict[str, Any]] = []
    all_results_lock = threading.Lock()
    max_workers = get_settings().max_concurrent_workers

    # Pre-extract file diffs (fast, single-threaded)
    file_defs: list[tuple[int, str, str]] = [
        (idx, fp, _extract_file_diff(diff, fp))
        for idx, fp in enumerate(cs_files, start=1)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(
                _evaluate_single_file, idx, fp, fdiff, event_summary, event_id,
            ): (idx, fp)
            for idx, fp, fdiff in file_defs
        }

        for future in concurrent.futures.as_completed(future_to_file):
            idx, fp = future_to_file[future]
            try:
                result = future.result()
                with all_results_lock:
                    all_results.append(result)
            except Exception as exc:
                logger.exception("Unexpected worker error for %s event_id=%s", fp, event_id)
                with all_results_lock:
                    all_results.append({
                        "file_path": fp, "file_idx": idx,
                        "dim_a": {}, "dim_b": {},
                        "error": "unexpected_error",
                        "error_message": str(exc),
                    })

            # Thread-safe progress tracking
            with completed_lock:
                completed_count += 1
                current = completed_count

            if progress_callback:
                try:
                    progress_callback(current, total_files)
                except Exception:
                    logger.warning("Progress callback failed at %d/%d", current, total_files)

    # ---- Step 3: Programmatic aggregation -----------------------------------
    all_dim_scores: dict[str, list[float]] = {
        "functionality_best_practices": [],
        "security_performance_health": [],
    }
    all_dim_summaries: dict[str, list[str]] = {
        "functionality_best_practices": [],
        "security_performance_health": [],
    }
    all_findings: list[dict[str, Any]] = []
    files_evaluated = 0
    rounds_count = 0

    for r in all_results:
        if r.get("error"):
            continue
        files_evaluated += 1
        for dim_key, dim_name in [
            ("dim_a", "functionality_best_practices"),
            ("dim_b", "security_performance_health"),
        ]:
            dim_result = r.get(dim_key, {})
            if dim_result and not dim_result.get("error"):
                all_dim_scores[dim_name].append(dim_result.get("score", 0))
                all_dim_summaries[dim_name].append(dim_result.get("summary", ""))
                all_findings.extend(dim_result.get("findings", []))
                rounds_count += 1

    _aggregate_and_update_event(event, all_dim_scores, all_dim_summaries, all_findings, cs_files)

    return {
        "status": event.status,
        "files_evaluated": files_evaluated,
        "rounds_completed": rounds_count,
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


def _persist_dimension_result(
    session: Session,
    event_id: int,
    file_idx: int,
    file_path: str,
    dimension: str,
    result: dict[str, Any],
    duration_ms: int,
) -> None:
    """Persist a single dimension evaluation result.

    Used by :func:`_evaluate_single_file` to record both dimension-A
    and dimension-B outcomes to the database through the worker's
    own session.
    """
    if result.get("error"):
        _persist_evaluation_round(
            session, event_id=event_id, round_number=file_idx,
            round_type=dimension, file_path=file_path, status="failed",
            input_summary={"file_path": file_path, "dimension": dimension},
            output_data=None, model_name=result.get("model_name", ""),
            tokens_used=result.get("tokens_used", 0), duration_ms=duration_ms,
            error_message=result["error"],
        )
    else:
        score = result.get("score", 0)
        summary = result.get("summary", "")
        findings = result.get("findings", [])
        _persist_evaluation_round(
            session, event_id=event_id, round_number=file_idx,
            round_type=dimension, file_path=file_path, status="success",
            input_summary={"file_path": file_path, "dimension": dimension},
            output_data={"score": score, "summary": summary, "findings": findings},
            score=score, model_name=result.get("model_name", ""),
            tokens_used=result.get("tokens_used", 0), duration_ms=duration_ms,
        )


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
