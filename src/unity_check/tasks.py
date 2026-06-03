import logging

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
from unity_check.llm import evaluate_with_llm
from unity_check.models import GithubEvent

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


def build_event_summary(event: GithubEvent) -> str:
    # Build a compact summary that can be fed directly to the model.
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


@celery_app.task(name="unity_check.process_github_event")
def process_github_event(event_id: int) -> dict[str, str]:
    db = SessionLocal()
    try:
        event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
        if event is None:
            return {"status": "not_found", "message": f"event_id={event_id} not found"}

        with db.begin_nested():
            event.status = "running"
            summary = build_event_summary(event)

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

            llm_result = evaluate_with_llm(
                event.event_type,
                event.action,
                summary,
                diff_content=event.diff_content or "",
            )
            event.risk_level = llm_result.get("risk_level", "unknown")
            event.evaluation_summary = llm_result.get("summary", summary)
            event.status = "success"
        db.commit()

        return {
            "status": "success",
            "event_id": str(event_id),
            "risk_level": str(event.risk_level),
        }
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
    finally:
        db.close()
