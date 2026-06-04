import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.db import Base, engine, get_db
from unity_check.git_service import extract_sha_from_payload
from unity_check.models import EvaluationRound, GithubEvent, RepoScanConfig, RuleResult
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
