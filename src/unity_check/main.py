import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.db import Base, engine, get_db
from unity_check.git_service import extract_sha_from_payload
from unity_check.models import EvaluationRound, GithubEvent, Notification, RepoScanConfig, RuleResult
from unity_check.rule_service import (
    ensure_repo_scan_config,
    get_analyze_paths,
    is_baseline_needed,
)
from unity_check.tasks import process_github_event, run_baseline_scan_task

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables are ready.")
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


def verify_github_signature(payload_bytes: bytes, signature_header: str | None) -> bool:
    # Allow local debug traffic when no secret is configured.
    if not settings.github_webhook_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.github_webhook_secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()
    provided = signature_header.split("=", maxsplit=1)[1]
    return hmac.compare_digest(expected, provided)


@app.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/webhook/github", status_code=status.HTTP_202_ACCEPTED)
async def receive_github_webhook(
    request: Request,
    x_github_event: str = Header(default="", alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    event_type = x_github_event.strip()
    payload_bytes = await request.body()

    if not verify_github_signature(payload_bytes, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    if event_type == "ping":
        logger.info("Ping event received (delivery=%s)", x_github_delivery)
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # Only handle the two event types included in phase-1 scope.
    if event_type not in {"push", "pull_request"}:
        raise HTTPException(status_code=400, detail="Only push and pull_request are supported.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    existed = None
    # Delivery ID is used as an idempotency key for duplicate retries.
    if x_github_delivery:
        existed = db.scalar(select(GithubEvent).where(GithubEvent.delivery_id == x_github_delivery))
    if existed is not None:
        return {"status": "accepted", "event_id": str(existed.id), "task_id": existed.task_id or ""}

    repository = ((payload.get("repository") or {}).get("full_name")) if isinstance(payload, dict) else None
    action = payload.get("action") if isinstance(payload, dict) else None

    # Extract before/after SHA from payload for git diff operations.
    before_sha, after_sha = extract_sha_from_payload(payload, event_type)

    event = GithubEvent(
        delivery_id=x_github_delivery,
        event_type=event_type,
        action=action,
        repository=repository,
        before_sha=before_sha,
        after_sha=after_sha,
        payload=payload,
        status="queued",
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Push heavy evaluation work to worker; API returns immediately.
    task = process_github_event.delay(event.id)
    event.task_id = task.id
    db.commit()

    logger.info("Webhook accepted: id=%s event=%s task=%s", event.id, event.event_type, task.id)
    return {"status": "accepted", "event_id": str(event.id), "task_id": task.id}


@app.get("/events/latest")
def get_latest_events(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, str | int | None]]:
    records = db.scalars(select(GithubEvent).order_by(desc(GithubEvent.id)).limit(max(1, min(limit, 100)))).all()
    return [
        {
            "id": item.id,
            "event_type": item.event_type,
            "action": item.action,
            "repository": item.repository,
            "after_sha": item.after_sha,
            "before_sha": item.before_sha,
            "status": item.status,
            "risk_level": item.risk_level,
            "task_id": item.task_id,
            "diff_size": item.diff_size,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in records
    ]


@app.get("/api/events")
def get_events_paginated(
    page: int = 1,
    page_size: int = 20,
    event_type: str | None = None,
    risk_level: str | None = None,
    status: str | None = None,
    repository: str | None = None,
    sort: str = "desc",
    db: Session = Depends(get_db),
) -> dict:
    """Paginated event list with optional filters.

    Query params:
        page: 1-based page number (default 1).
        page_size: items per page (default 20, max 100).
        event_type: filter by push / pull_request.
        risk_level: filter by final_risk_level (low/medium/high/critical).
        status: filter by event status (queued/running/success/failed).
        repository: filter by repository name (exact match).
        sort: created_at order — "desc" (default) or "asc".
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    offset = (page - 1) * page_size

    base = select(GithubEvent)
    count_base = select(func.count(GithubEvent.id))

    if event_type:
        base = base.where(GithubEvent.event_type == event_type)
        count_base = count_base.where(GithubEvent.event_type == event_type)
    if risk_level:
        base = base.where(GithubEvent.final_risk_level == risk_level)
        count_base = count_base.where(GithubEvent.final_risk_level == risk_level)
    if status:
        base = base.where(GithubEvent.status == status)
        count_base = count_base.where(GithubEvent.status == status)
    if repository:
        base = base.where(GithubEvent.repository == repository)
        count_base = count_base.where(GithubEvent.repository == repository)

    total = db.scalar(count_base) or 0
    total_pages = max(1, (total + page_size - 1) // page_size)

    order = GithubEvent.created_at.desc() if sort == "desc" else GithubEvent.created_at.asc()
    rows = db.scalars(
        base.order_by(order).limit(page_size).offset(offset)
    ).all()

    items = [
        {
            "id": item.id,
            "delivery_id": item.delivery_id,
            "event_type": item.event_type,
            "action": item.action,
            "repository": item.repository,
            "after_sha": item.after_sha,
            "diff_size": item.diff_size,
            "status": item.status,
            "overall_score": item.overall_score,
            "final_risk_level": item.final_risk_level,
            "recommendation": item.recommendation,
            "executive_summary": item.executive_summary,
            "task_id": item.task_id,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        for item in rows
    ]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


@app.get("/events/{event_id}")
def get_event_detail(event_id: int, db: Session = Depends(get_db)) -> dict[str, str | int | None]:
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return {
        "id": event.id,
        "delivery_id": event.delivery_id,
        "event_type": event.event_type,
        "action": event.action,
        "repository": event.repository,
        "after_sha": event.after_sha,
        "before_sha": event.before_sha,
        "clone_path": event.clone_path,
        "diff_content": event.diff_content,
        "diff_size": event.diff_size,
        "status": event.status,
        "risk_level": event.risk_level,
        "evaluation_summary": event.evaluation_summary,
        "error_message": event.error_message,
        "task_id": event.task_id,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Rule results
# ---------------------------------------------------------------------------


@app.get("/events/{event_id}/rules")
def get_event_rules(
    event_id: int,
    severity: str | None = None,
    category: str | None = None,
    rule_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict[str, str | int | None]]:
    """Return rule violations for an event, with optional filters."""
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    stmt = select(RuleResult).where(RuleResult.event_id == event_id)
    if severity:
        stmt = stmt.where(RuleResult.severity == severity.capitalize())
    if category:
        stmt = stmt.where(RuleResult.category == category)
    if rule_id:
        stmt = stmt.where(RuleResult.rule_id == rule_id)
    stmt = (
        stmt
        .order_by(RuleResult.severity.desc(), RuleResult.file_path, RuleResult.line_number)
        .limit(min(limit, 500))
        .offset(max(0, offset))
    )

    rows = db.scalars(stmt).all()
    return [
        {
            "id": r.id,
            "rule_id": r.rule_id,
            "rule_name": r.rule_name,
            "file_path": r.file_path,
            "line_number": r.line_number,
            "column_number": r.column_number,
            "severity": r.severity,
            "category": r.category,
            "message": r.message,
            "snippet": r.snippet,
            "scan_type": r.scan_type,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@app.get("/rules/stats")
def get_rules_stats(
    repository: str | None = None,
    days: int = 30,
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated rule statistics: top rules, severity breakdown, trend."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    # Base query
    stmt = select(RuleResult)
    if repository:
        stmt = stmt.join(GithubEvent).where(
            GithubEvent.repository == repository,
            RuleResult.created_at >= since,
        )
    else:
        stmt = stmt.where(RuleResult.created_at >= since)

    rows = db.scalars(stmt).all()

    # Severity breakdown
    severity_counts: dict[str, int] = {}
    # Top rules
    rule_counts: dict[str, int] = {}
    # Top files
    file_counts: dict[str, int] = {}

    for r in rows:
        sev = r.severity.lower()
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        key = f"{r.rule_id}: {r.rule_name}"
        rule_counts[key] = rule_counts.get(key, 0) + 1
        file_counts[r.file_path] = file_counts.get(r.file_path, 0) + 1

    top_rules = sorted(rule_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    top_files = sorted(file_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "total": len(rows),
        "severity_breakdown": severity_counts,
        "top_rules": [{"rule": k, "count": v} for k, v in top_rules],
        "top_files": [{"file": k, "count": v} for k, v in top_files],
        "since_days": days,
    }


# ---------------------------------------------------------------------------
# Repository scan configuration
# ---------------------------------------------------------------------------

def _repo_name_from_path(repo: str) -> str:
    """Normalise a URL-encoded or raw repository name for lookup."""
    return repo.strip().lower()


@app.get("/repos/{repo_name}/config")
def get_repo_config(
    repo_name: str,
    db: Session = Depends(get_db),
) -> dict:
    """Return scan configuration for a repository."""
    name = _repo_name_from_path(repo_name)
    config = db.scalar(
        select(RepoScanConfig).where(RepoScanConfig.repository == name)
    )
    if config is None:
        # Return default config without creating it.
        return {
            "repository": name,
            "analyze_paths": get_analyze_paths(name, db),
            "is_baseline_scanned": False,
            "baseline_scan_status": None,
            "baseline_total_files": None,
            "baseline_total_issues": None,
        }
    return {
        "repository": config.repository,
        "analyze_paths": config.analyze_paths,
        "is_baseline_scanned": config.is_baseline_scanned,
        "baseline_scan_status": config.baseline_scan_status,
        "baseline_total_files": config.baseline_total_files,
        "baseline_total_issues": config.baseline_total_issues,
    }


@app.put("/repos/{repo_name}/config")
def update_repo_config(
    repo_name: str,
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """Create or update scan configuration for a repository.

    Request body (all fields optional):
    {
        "repository": "owner/repo",
        "analyze_paths": ["Assets/Scripts", "Assets/Editor"],
        "is_baseline_scanned": false
    }
    """
    name = _repo_name_from_path(repo_name)
    config = ensure_repo_scan_config(name, db)

    if "repository" in body and body["repository"]:
        config.repository = str(body["repository"])
    if "analyze_paths" in body and isinstance(body["analyze_paths"], list):
        config.analyze_paths = [str(p) for p in body["analyze_paths"]]
    if "is_baseline_scanned" in body:
        config.is_baseline_scanned = bool(body["is_baseline_scanned"])

    db.flush()
    return {
        "repository": config.repository,
        "analyze_paths": config.analyze_paths,
        "is_baseline_scanned": config.is_baseline_scanned,
        "baseline_scan_status": config.baseline_scan_status,
        "message": "Configuration updated.",
    }


@app.post("/repos/{repo_name}/baseline-scan")
def trigger_baseline_scan(
    repo_name: str,
    db: Session = Depends(get_db),
) -> dict:
    """Manually trigger a baseline scan for a repository.

    Returns immediately with a Celery task_id; the scan runs async.
    """
    name = _repo_name_from_path(repo_name)
    config = ensure_repo_scan_config(name, db)

    if config.baseline_scan_status == "running":
        return {
            "status": "conflict",
            "message": "A baseline scan is already running for this repository.",
        }

    # We need the repo's local path.  Since this is a manual trigger, use the
    # configured git_clone_base_dir + repo name to locate it.
    import os
    from unity_check.git_service import _repo_name_from_url as git_repo_name

    clone_base = os.path.abspath(settings.git_clone_base_dir)
    # Try to find existing clone path; for manual trigger we try SSH format.
    bare_candidate = os.path.join(
        clone_base, f"{git_repo_name(f'git@github.com:{name}.git')}.git"
    )
    if not os.path.isdir(bare_candidate):
        # Try https format
        bare_candidate = os.path.join(
            clone_base, f"{git_repo_name(f'https://github.com/{name}.git')}.git"
        )

    if not os.path.isdir(bare_candidate):
        raise HTTPException(
            status_code=400,
            detail="Repository has not been cloned yet. "
                    "Trigger a webhook first to initialise the bare clone.",
        )

    config.baseline_scan_status = "pending"
    db.flush()

    task = run_baseline_scan_task.delay(name, bare_candidate)
    config.baseline_scan_status = "running"
    db.flush()

    return {
        "status": "accepted",
        "repository": name,
        "task_id": task.id,
        "message": "Baseline scan dispatched.",
    }


# ---------------------------------------------------------------------------
# P3: Multi-round evaluation endpoints
# ---------------------------------------------------------------------------


@app.get("/events/{event_id}/evaluations")
def get_event_evaluations(event_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Return all evaluation rounds for an event, ordered by round_number."""
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    rounds = db.scalars(
        select(EvaluationRound)
        .where(EvaluationRound.event_id == event_id)
        .order_by(EvaluationRound.round_number)
    ).all()

    return [
        {
            "id": r.id,
            "round_number": r.round_number,
            "round_type": r.round_type,
            "status": r.status,
            "score": r.score,
            "model_name": r.model_name,
            "tokens_used": r.tokens_used,
            "duration_ms": r.duration_ms,
            "error_message": r.error_message,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rounds
    ]


@app.get("/events/{event_id}/assessment")
def get_event_assessment(event_id: int, db: Session = Depends(get_db)) -> dict:
    """Return the final assessment for an event.

    Combines the GithubEvent-level summary fields with an aggregated view
    of the evaluation rounds.
    """
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    rounds = db.scalars(
        select(EvaluationRound)
        .where(EvaluationRound.event_id == event_id)
        .order_by(EvaluationRound.round_number)
    ).all()

    return {
        "event_id": event.id,
        "status": event.status,
        "overall_score": event.overall_score,
        "final_risk_level": event.final_risk_level,
        "recommendation": event.recommendation,
        "executive_summary": event.executive_summary,
        "rounds": [
            {
                "round_number": r.round_number,
                "round_type": r.round_type,
                "status": r.status,
                "score": r.score,
                "tokens_used": r.tokens_used,
                "duration_ms": r.duration_ms,
            }
            for r in rounds
        ],
        "total_tokens_used": sum(r.tokens_used or 0 for r in rounds),
        "total_duration_ms": sum(r.duration_ms or 0 for r in rounds),
    }


@app.post("/events/{event_id}/re-evaluate")
def re_evaluate_event(event_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete existing evaluation rounds and re-trigger the full pipeline."""
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    # Remove old evaluation rounds.
    db.query(EvaluationRound).filter(EvaluationRound.event_id == event_id).delete()
    db.flush()

    # Reset event status and evaluation fields.
    event.status = "queued"
    event.risk_level = None
    event.evaluation_summary = None
    event.overall_score = None
    event.final_risk_level = None
    event.recommendation = None
    event.executive_summary = None
    db.flush()

    # Re-enqueue the pipeline task.
    task = process_github_event.delay(event.id)
    event.task_id = task.id
    db.commit()

    return {
        "status": "accepted",
        "event_id": event.id,
        "task_id": task.id,
        "message": "Re-evaluation triggered.",
    }


# ---------------------------------------------------------------------------
# P4: Notification endpoints
# ---------------------------------------------------------------------------


@app.get("/api/notifications")
def get_notifications(
    event_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Return notifications, optionally filtered by event_id or status."""
    from sqlalchemy import select as sa_select

    stmt = sa_select(Notification)
    if event_id is not None:
        stmt = stmt.where(Notification.event_id == event_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    stmt = (
        stmt
        .order_by(Notification.created_at.desc())
        .limit(min(limit, 200))
        .offset(max(0, offset))
    )

    rows = db.scalars(stmt).all()
    return [
        {
            "id": n.id,
            "event_id": n.event_id,
            "channel": n.channel,
            "trigger_reason": n.trigger_reason,
            "risk_level": n.risk_level,
            "message_content": n.message_content,
            "webhook_url": n.webhook_url,
            "status": n.status,
            "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            "error_message": n.error_message,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        }
        for n in rows
    ]


@app.post("/api/notifications/{notification_id}/send-status")
def update_notification_send_status(
    notification_id: int,
    body: dict,
    db: Session = Depends(get_db),
) -> dict:
    """Callback endpoint for the external tool platform to update delivery status.

    Request body:
    {
        "status": "sent" | "failed",
        "error_message": "optional error detail"
    }
    """
    from unity_check.notification_service import update_notification_status

    new_status = str(body.get("status", "pending"))
    if new_status not in ("sent", "failed", "pending"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{new_status}'. Must be sent, failed, or pending.",
        )

    error_msg = body.get("error_message")
    notif = update_notification_status(
        notification_id, new_status, db, error_message=str(error_msg) if error_msg else None
    )
    if notif is None:
        raise HTTPException(status_code=404, detail=f"Notification {notification_id} not found")

    return {
        "id": notif.id,
        "status": notif.status,
        "sent_at": notif.sent_at.isoformat() if notif.sent_at else None,
        "message": "Status updated.",
    }


# ---------------------------------------------------------------------------
# P4: Dashboard / stats API
# ---------------------------------------------------------------------------


@app.get("/api/dashboard/summary")
def dashboard_summary(
    days: int = 30,
    repository: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Aggregated dashboard summary: event counts, risk distribution, score stats."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    # Base query
    base = select(GithubEvent).where(GithubEvent.created_at >= since)
    if repository:
        base = base.where(GithubEvent.repository == repository)

    events = db.scalars(base.order_by(GithubEvent.created_at.desc())).all()

    total = len(events)
    risk_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    scores: list[float] = []

    for e in events:
        # Risk distribution
        risk = (e.final_risk_level or "unknown").lower()
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        # Event type
        t = e.event_type or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        # Scores
        if e.overall_score is not None:
            scores.append(e.overall_score)

    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    # Recent events (last 10)
    recent = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "repository": e.repository,
            "status": e.status,
            "overall_score": e.overall_score,
            "final_risk_level": e.final_risk_level,
            "recommendation": e.recommendation,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events[:10]
    ]

    # Status breakdown
    status_counts: dict[str, int] = {}
    for e in events:
        s = e.status or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    return {
        "total_events": total,
        "risk_distribution": risk_counts,
        "event_type_distribution": type_counts,
        "status_distribution": status_counts,
        "average_score": avg_score,
        "recent_events": recent,
        "since_days": days,
    }


@app.get("/api/dashboard/trends")
def dashboard_trends(
    days: int = 30,
    repository: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Daily trend data: score & event count per day."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    base = select(GithubEvent).where(GithubEvent.created_at >= since)
    if repository:
        base = base.where(GithubEvent.repository == repository)

    events = db.scalars(base.order_by(GithubEvent.created_at)).all()

    # Group by date
    daily: dict[str, dict[str, int | float | list]] = {}
    for e in events:
        day = e.created_at.strftime("%Y-%m-%d") if e.created_at else "unknown"
        if day not in daily:
            daily[day] = {"date": day, "count": 0, "scores": []}
        daily[day]["count"] += 1
        if e.overall_score is not None:
            daily[day]["scores"].append(e.overall_score)

    result = []
    for day, data in sorted(daily.items()):
        scores_list = data["scores"]
        result.append({
            "date": day,
            "event_count": data["count"],
            "avg_score": round(sum(scores_list) / len(scores_list), 1) if scores_list else None,
        })

    return result


@app.get("/api/dashboard/issue-distribution")
def dashboard_issue_distribution(
    days: int = 30,
    repository: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Rule and semantic finding distribution: by category, severity, source."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    # Rule results
    rule_base = (
        select(RuleResult)
        .join(GithubEvent)
        .where(GithubEvent.created_at >= since)
    )
    if repository:
        rule_base = rule_base.where(GithubEvent.repository == repository)
    rules = db.scalars(rule_base).all()

    # Category breakdown
    rule_category_counts: dict[str, int] = {}
    rule_severity_counts: dict[str, int] = {}
    for r in rules:
        cat = (r.category or "uncategorized").lower()
        rule_category_counts[cat] = rule_category_counts.get(cat, 0) + 1
        sev = r.severity.lower()
        rule_severity_counts[sev] = rule_severity_counts.get(sev, 0) + 1

    # Semantic findings from evaluation_rounds (R2 output)
    eval_base = (
        select(EvaluationRound)
        .join(GithubEvent)
        .where(
            GithubEvent.created_at >= since,
            EvaluationRound.round_number == 2,
            EvaluationRound.status == "success",
        )
    )
    if repository:
        eval_base = eval_base.where(GithubEvent.repository == repository)
    eval_rounds = db.scalars(eval_base).all()

    semantic_category_counts: dict[str, int] = {}
    semantic_severity_counts: dict[str, int] = {}
    for er in eval_rounds:
        findings = (er.output_data or {}).get("findings", []) or []
        for f in findings:
            cat = (f.get("category", "unknown") or "unknown").lower()
            semantic_category_counts[cat] = semantic_category_counts.get(cat, 0) + 1
            sev = (f.get("severity", "unknown") or "unknown").lower()
            semantic_severity_counts[sev] = semantic_severity_counts.get(sev, 0) + 1

    return {
        "rules": {
            "total": len(rules),
            "by_category": rule_category_counts,
            "by_severity": rule_severity_counts,
        },
        "semantic": {
            "total": sum(semantic_category_counts.values()),
            "by_category": semantic_category_counts,
            "by_severity": semantic_severity_counts,
        },
        "since_days": days,
    }


@app.get("/api/stats/scores")
def stats_scores(
    from_date: str | None = None,
    to_date: str | None = None,
    repository: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Score statistics over a date range.

    Query params: from_date / to_date in ISO format (e.g. 2026-05-01).
    """
    from datetime import datetime, timezone

    base = select(
        GithubEvent.overall_score,
        GithubEvent.final_risk_level,
        GithubEvent.created_at,
    )
    if from_date:
        try:
            f = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
            base = base.where(GithubEvent.created_at >= f)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid from_date format")
    if to_date:
        try:
            t = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc)
            base = base.where(GithubEvent.created_at <= t)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid to_date format")
    if repository:
        base = base.where(GithubEvent.repository == repository)
    base = base.where(GithubEvent.overall_score.isnot(None)).order_by(GithubEvent.created_at)

    rows = db.execute(base).all()
    scores = [r[0] for r in rows if r[0] is not None]

    if not scores:
        return {"count": 0, "min": None, "max": None, "avg": None, "scores": []}

    return {
        "count": len(scores),
        "min": round(min(scores), 1),
        "max": round(max(scores), 1),
        "avg": round(sum(scores) / len(scores), 1),
        "scores": [round(s, 1) for s in scores],
    }


@app.get("/api/stats/hotspots")
def stats_hotspots(
    limit: int = 10,
    days: int = 30,
    repository: str | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Top files by rule-result count (hotspots)."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    base = (
        select(RuleResult.file_path, func.count().label("cnt"))
        .join(GithubEvent)
        .where(GithubEvent.created_at >= since)
    )
    if repository:
        base = base.where(GithubEvent.repository == repository)
    base = (
        base
        .group_by(RuleResult.file_path)
        .order_by(func.count().desc())
        .limit(min(limit, 50))
    )

    rows = db.execute(base).all()
    return [{"file": r[0], "count": r[1]} for r in rows]
