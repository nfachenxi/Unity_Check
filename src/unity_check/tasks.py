import logging
import os

from sqlalchemy import select

from unity_check.celery_app import celery_app
from unity_check.config import get_settings
from unity_check.db import SessionLocal
from unity_check.git_service import (
    GitServiceError,
    ensure_bare_repo,
    extract_clone_url_from_payload,
    get_diff,
)
from unity_check.models import GithubEvent
from unity_check.orchestrator import run_evaluation_pipeline
from unity_check.rule_service import (
    ensure_repo_scan_config,
    extract_cs_files_from_diff,
    filter_analyze_targets,
    get_analyze_paths,
    is_baseline_needed,
    run_roslyn_analysis,
    save_rule_results,
)

logger = logging.getLogger(__name__)


def _resolve_clone_url(event: GithubEvent) -> str | None:
    """Determine the clone URL for an event.

    Priority: payload → config GITHUB_REMOTE_REPO fallback.
    """
    url = extract_clone_url_from_payload(event.payload or {})
    if url:
        return url
    settings = get_settings()
    if settings.github_remote_repo:
        return settings.github_remote_repo
    return None


def _run_git_workflow(event: GithubEvent) -> None:
    """Execute clone/fetch/diff for the event and write results back.

    Side-effects on event: clone_path, diff_content, diff_size.
    When no clone URL can be resolved, the event is left unchanged.
    """
    clone_url = _resolve_clone_url(event)
    if not clone_url:
        logger.warning(
            "No clone URL resolved for event_id=%s – skipping git diff",
            event.id,
        )
        return

    before_sha = event.before_sha or ""
    after_sha = event.after_sha or ""

    if not after_sha:
        logger.warning(
            "No after_sha available for event_id=%s – skipping git diff",
            event.id,
        )
        return

    bare_path = ensure_bare_repo(clone_url)
    event.clone_path = bare_path

    diff = get_diff(bare_path, before_sha, after_sha)
    event.diff_content = diff
    event.diff_size = len(diff.encode("utf-8"))


def _ensure_baseline_scan(
    event: GithubEvent, db: SessionLocal
) -> None:
    """Check whether the event's repository needs a first-time baseline scan.

    When a baseline is needed a *separate* Celery task is spawned so the
    current webhook task can continue without delay.
    """
    repo = (event.repository or "").strip()
    if not repo:
        return

    # Create the config row if it doesn't exist yet.
    ensure_repo_scan_config(repo, db)

    if not is_baseline_needed(repo, db):
        return

    logger.info(
        "Dispatching baseline scan for repo=%s event_id=%s", repo, event.id
    )
    # Clone path must exist (set by _run_git_workflow).
    repo_path = event.clone_path
    if not repo_path:
        logger.warning(
            "No clone_path for event_id=%s – cannot baseline scan", event.id
        )
        return

    run_baseline_scan_task.delay(repo, repo_path)


def _run_roslyn_incremental(
    event: GithubEvent, db: SessionLocal
) -> int:
    """Run Roslyn analysis on the .cs files changed in this event's diff.

    Returns the number of ``RuleResult`` rows persisted.
    """
    repo = (event.repository or "").strip()
    diff = (event.diff_content or "").strip()
    if not diff:
        logger.info("No diff content for event_id=%s – skipping Roslyn", event.id)
        return 0

    # 1. Extract changed .cs files from the diff.
    cs_files = extract_cs_files_from_diff(diff)
    if not cs_files:
        logger.info("No .cs files in diff for event_id=%s", event.id)
        return 0

    # 2. Filter against repo's analyze_paths.
    analyze_paths = get_analyze_paths(repo, db)
    targets = filter_analyze_targets(cs_files, analyze_paths)
    if not targets:
        logger.info(
            "All %d .cs file(s) filtered out by analyze_paths=%s for event_id=%s",
            len(cs_files), analyze_paths, event.id,
        )
        return 0

    # 3. Read full file content from the bare repo at after_sha.
    #    We use `git show <after_sha>:<path>` for each file.
    import git
    bare_path = event.clone_path
    if not bare_path or not os.path.isdir(bare_path):
        logger.warning("No clone_path for event_id=%s – cannot read file content", event.id)
        return 0

    file_payload: list[tuple[str, str]] = []
    try:
        repo_obj = git.Repo(bare_path)
    except Exception as exc:
        logger.warning("Cannot open bare repo %s: %s", bare_path, exc)
        return 0

    after_sha = event.after_sha or "HEAD"
    for rel_path in targets:
        try:
            content = repo_obj.git.show(f"{after_sha}:{rel_path}")
        except Exception as exc:
            logger.warning("git show %s:%s failed: %s", after_sha, rel_path, exc)
            continue
        file_payload.append((rel_path, content))

    if not file_payload:
        return 0

    # 4. Call Roslyn.
    diagnostics = run_roslyn_analysis(file_payload)
    if not diagnostics:
        return 0

    # 5. Persist.
    return save_rule_results(
        db, event_id=int(event.id), diagnostics=diagnostics, scan_type="incremental",
    )


@celery_app.task(name="unity_check.process_github_event")
def process_github_event(event_id: int) -> dict[str, str]:
    db = SessionLocal()
    try:
        event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
        if event is None:
            return {"status": "not_found", "message": f"event_id={event_id} not found"}

        with db.begin_nested():
            event.status = "running"

            # Git workflow: clone/fetch → diff (runs outside the savepoint).
            # On failure we catch GitServiceError, record the error, and
            # still attempt a blind LLM evaluation.
            try:
                _run_git_workflow(event)
            except GitServiceError as exc:
                logger.warning(
                    "Git workflow failed for event_id=%s: %s", event_id, exc
                )
                event.error_message = str(exc)

            # Baseline check: fire-and-forget if this repo hasn't been scanned.
            _ensure_baseline_scan(event, db)

            # Roslyn incremental analysis on changed .cs files.
            rule_count = _run_roslyn_incremental(event, db)
            if rule_count > 0:
                logger.info(
                    "Roslyn incremental analysis added %d rule results for event_id=%s",
                    rule_count, event_id,
                )

            # Per-file, per-dimension evaluation pipeline.
            pipeline_result = run_evaluation_pipeline(event, db)
            # The orchestrator sets all evaluation fields on event. No further
            # assignment needed here.
            event_status = pipeline_result.get("status", "success")
    except Exception as exc:
        # Persist failure details so the pipeline can be traced from UI/query endpoints.
        logger.exception("Task failed for event_id=%s", event_id)
        failed = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
        if failed is not None:
            failed.status = "failed"
            failed.error_message = failed.error_message or ""
            if str(exc) not in failed.error_message:
                failed.error_message = (
                    f"{failed.error_message}; {exc}" if failed.error_message else str(exc)
                )
            db.commit()
        return {"status": "failed", "event_id": str(event_id), "error": str(exc)}
    else:
        db.commit()
        return {
            "status": event_status,
            "event_id": str(event_id),
            "risk_level": str(event.final_risk_level),
        }
    finally:
        db.close()


@celery_app.task(name="unity_check.run_baseline_scan_task")
def run_baseline_scan_task(repository: str, repo_path: str) -> dict[str, str]:
    """Celery task that executes a full baseline scan for *repository*.

    This is dispatched asynchronously from ``_ensure_baseline_scan`` so the
    webhook response path is not blocked.
    """
    from unity_check.rule_service import run_baseline_scan

    db = SessionLocal()
    try:
        result = run_baseline_scan(repository, repo_path, db)
        db.commit()
        logger.info(
            "Baseline scan finished for %s: total_files=%s total_issues=%s",
            repository,
            result.get("total_files", 0),
            result.get("total_issues", 0),
        )
        return {
            "status": str(result.get("status", "unknown")),
            "repository": repository,
            "total_files": str(result.get("total_files", 0)),
            "total_issues": str(result.get("total_issues", 0)),
        }
    except Exception as exc:
        logger.exception("Baseline scan failed for %s", repository)
        from unity_check.rule_service import _update_scan_config
        _update_scan_config(db, repository, baseline_scan_status="failed")
        db.commit()
        return {"status": "failed", "repository": repository, "error": str(exc)}
    finally:
        db.close()
