"""Notification service: threshold judgment, message building, persistence.

Actual delivery to WeCom / Feishu is delegated to an independent tool
platform.  This module is responsible for:

1. Deciding *whether* a notification should be generated.
2. Building the message payload (Markdown for WeCom, interactive card JSON
   for Feishu).
3. Persisting the notification record to the database.
4. Providing a callback endpoint that the tool platform calls to update
   delivery status.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.models import GithubEvent, Notification

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Threshold rules (from the plan)
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
# Message builders
# ---------------------------------------------------------------------------


def _build_wecom_markdown(event: GithubEvent) -> str:
    """Build an enterprise-WeChat markdown notification message."""
    repo = event.repository or "unknown"
    risk = (event.final_risk_level or "unknown").upper()
    score = event.overall_score
    score_str = f"{score:.0f}" if score is not None else "N/A"
    recommendation = (event.recommendation or "needs_review").replace("_", " ")

    lines = [
        f"## 🔔 Unity Check 评估结果",
        f"",
        f"**仓库**: {repo}",
        f"**事件类型**: {event.event_type}",
        f"**风险等级**: <font color=\"warning\">{risk}</font>",
        f"**综合评分**: {score_str}/100",
        f"**建议**: {recommendation}",
        f"",
    ]

    summary = (event.executive_summary or "").strip()
    if summary:
        lines.append(f"**摘要**: {summary}")
        lines.append("")

    lines.append(f"[查看详情](http://localhost:8000/events/{event.id})")
    return "\n".join(lines)


def _build_feishu_card(event: GithubEvent) -> dict[str, Any]:
    """Build a Feishu interactive-card JSON payload."""
    repo = event.repository or "unknown"
    risk = (event.final_risk_level or "unknown").upper()
    score = event.overall_score
    score_str = f"{score:.0f}" if score is not None else "N/A"

    risk_color: dict[str, str] = {
        "critical": "red",
        "high": "red",
        "medium": "orange",
        "low": "green",
        "unknown": "grey",
    }

    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "Unity Check 评估结果"},
                "template": risk_color.get((risk or "unknown").lower(), "grey"),
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**仓库**: {repo}"},
                },
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**风险等级**: {risk}"},
                },
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**综合评分**: {score_str}/100"},
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**建议**: {(event.recommendation or 'needs_review').replace('_', ' ')}",
                    },
                },
                {
                    "tag": "hr",
                },
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (event.executive_summary or "无摘要")[:500],
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看详情"},
                            "type": "primary",
                            "url": f"http://localhost:8000/events/{event.id}",
                        }
                    ],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_and_persist_notifications(
    event: GithubEvent, db: Session
) -> list[Notification]:
    """Build notification messages for all configured channels and persist them.

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

    records: list[Notification] = []

    # WeCom
    wecom_md = _build_wecom_markdown(event)
    records.append(
        Notification(
            event_id=int(event.id),
            channel="wecom",
            trigger_reason=reason,
            risk_level=event.final_risk_level,
            message_content=wecom_md,
            webhook_url=None,  # filled by tool platform
            status="pending",
        )
    )

    # Feishu
    feishu_card = _build_feishu_card(event)
    records.append(
        Notification(
            event_id=int(event.id),
            channel="feishu",
            trigger_reason=reason,
            risk_level=event.final_risk_level,
            message_content=str(feishu_card),  # stored as JSON string in text column
            webhook_url=None,
            status="pending",
        )
    )

    if records:
        db.add_all(records)
        db.flush()
        logger.info(
            "Persisted %d notification(s) for event_id=%s (reason=%s)",
            len(records), event.id, reason,
        )

    return records


def get_notifications_for_event(
    event_id: int, db: Session
) -> list[Notification]:
    """Return all notifications for an event, ordered by creation time."""
    from sqlalchemy import select

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
    from sqlalchemy import select

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
