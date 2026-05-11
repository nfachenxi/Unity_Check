import logging

from sqlalchemy import select

from unity_check.celery_app import celery_app
from unity_check.db import SessionLocal
from unity_check.llm import evaluate_with_llm
from unity_check.models import GithubEvent

logger = logging.getLogger(__name__)


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

        event.status = "running"
        db.commit()

        # Current stage uses one-pass model triage and persists normalized result fields.
        summary = build_event_summary(event)
        llm_result = evaluate_with_llm(event.event_type, event.action, summary)
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
            failed.error_message = str(exc)
            db.commit()
        return {"status": "failed", "event_id": str(event_id), "error": str(exc)}
    finally:
        db.close()
