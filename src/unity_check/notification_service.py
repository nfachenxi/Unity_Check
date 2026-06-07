"""Notification service: threshold judgment, generic message building, persistence.

Actual delivery is delegated to an independent tool platform.  This module is
responsible for:

1. Deciding *whether* a notification should be generated.
2. Building a generic JSON message payload.
3. Persisting the notification record to the database.
4. Providing a callback endpoint that the tool platform calls to update
   delivery status.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.models import GithubEvent, Notification, RuleResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Threshold rules
# ---------------------------------------------------------------------------


def _should_notify(event: GithubEvent) -> tuple[bool, str]:
    """Determine whether *event* warrants a notification.

    Returns (should_notify, trigger_reason).
    """
    risk = (event.final_risk_level or "").lower()
    score = event.overall_score

    settings = get_settings()
    score_threshold = settings.notify_score_threshold

    # critical / high → always notify
    if risk in ("critical", "high"):
        return True, risk

    # medium + score < threshold → notify
    if risk == "medium" and score is not None and score < score_threshold:
        return True, "medium_low_score"

    # low → never notify
    return False, ""


# ---------------------------------------------------------------------------
# Generic message builder
# ---------------------------------------------------------------------------


def _build_generic_message(event: GithubEvent) -> str:
    """Build a generic JSON notification message for external delivery."""
    repo = event.repository or "unknown"

    # Collect top issues from evaluation rounds
    from sqlalchemy.orm import Session as _Session
    from unity_check.db import SessionLocal

    # Use the passed event's associated rounds if available (via relationship)
    # otherwise build from known fields
    dim_scores = {}
    if event.dimension_a_score is not None:
        dim_scores["functionality_best_practices"] = event.dimension_a_score
    if event.dimension_b_score is not None:
        dim_scores["security_performance_health"] = event.dimension_b_score

    dim_summaries = {}
    if event.dimension_a_summary:
        dim_summaries["functionality_best_practices"] = event.dimension_a_summary
    if event.dimension_b_summary:
        dim_summaries["security_performance_health"] = event.dimension_b_summary

    # Rule violations count — need a session to query
    # Build without DB access first, caller can enrich
    payload = {
        "type": "code_review_alert",
        "version": "2.0",
        "event_id": int(event.id),
        "repository": repo,
        "event_type": event.event_type,
        "status": event.status,
        "overall_score": event.overall_score,
        "final_risk_level": event.final_risk_level,
        "recommendation": event.recommendation,
        "executive_summary": event.executive_summary,
        "dimension_scores": dim_scores,
        "dimension_summaries": dim_summaries,
        "detail_url": f"http://localhost:8000/api/events/{event.id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_and_persist_notifications(
    event: GithubEvent, db: Session
) -> list[Notification]:
    """Build and persist a single generic notification if thresholds are met.

    Called after a successful evaluation pipeline run.  Returns the list of
    persisted ``Notification`` rows (empty if thresholds are not met).
    """
    should, reason = _should_notify(event)
    if not should:
        logger.info(
            "Notification skipped for event_id=%s (risk=%s score=%s)",
            event.id, event.final_risk_level, event.overall_score,
        )
        return []

    message_json = _build_generic_message(event)
    notif = Notification(
        event_id=int(event.id),
        channel="generic",
        trigger_reason=reason,
        risk_level=event.final_risk_level,
        message_content=message_json,
        webhook_url=None,
        status="pending",
    )
    db.add(notif)
    db.flush()
    logger.info(
        "Persisted 1 notification for event_id=%s (reason=%s)",
        event.id, reason,
    )
    return [notif]


def get_notifications_for_event(
    event_id: int, db: Session
) -> list[Notification]:
    """Return all notifications for an event, ordered by creation time."""
    return list(
        db.scalars(
            select(Notification)
            .where(Notification.event_id == event_id)
            .order_by(Notification.created_at)
        ).all()
    )


def update_notification_status(
    notification_id: int,
    status: str,
    db: Session,
    *,
    error_message: str | None = None,
) -> Notification | None:
    """Update delivery status for a notification (called by tool platform callback)."""
    notif = db.scalar(
        select(Notification).where(Notification.id == notification_id)
    )
    if notif is None:
        return None

    notif.status = status
    if error_message:
        notif.error_message = error_message
    if status == "sent":
        notif.sent_at = datetime.now(timezone.utc)
    db.flush()
    return notif
