import json
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from unity_check import repository_service, webhook_service
from unity_check.config import get_settings
from unity_check.db import Base, engine, get_db, run_migrations
from unity_check.git_service import extract_sha_from_payload
from unity_check.models import EvaluationRound, GithubEvent, Repository, SystemSetting, Task

settings = get_settings()

# In-memory login attempt tracking: IP -> {count, banned_until}
_login_attempts: dict[str, dict] = {}

logging.basicConfig(
    level=getattr(logging, settings.app_log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


class RepositoryCreate(BaseModel):
    """Request body for POST /api/repositories."""
    name: str
    alias: str | None = None
    clone_url: str | None = None
    webhook_secret: str | None = None
    ssh_key_path: str | None = None
    branch_filter: str | None = None
    is_active: bool | None = None


class RepositoryUpdate(BaseModel):
    """Request body for PUT /api/repositories/{id}."""
    alias: str | None = None
    clone_url: str | None = None
    webhook_secret: str | None = None
    ssh_key_path: str | None = None
    branch_filter: str | None = None
    is_active: bool | None = None
    status: str | None = None


def _resolve_webhook_secret(repo: Repository, db: Session) -> str | None:
    """Determine effective webhook secret for a repository.

    Resolution order:
    1. Repo-level ``webhook_secret=""`` (empty string) → explicitly no secret.
    2. Repo-level ``webhook_secret`` is a non-empty string → custom secret.
    3. Repo-level ``webhook_secret`` is ``None`` → generic secret (DB override, then ``.env``).
    """
    if repo.webhook_secret == "":
        return None
    if repo.webhook_secret is not None:
        return repo.webhook_secret
    # Fallback to generic secret: DB override > env
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "generic_webhook_secret"))
    if setting and setting.value:
        return setting.value
    return settings.generic_webhook_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure SQLite data directory exists
    db_path = settings.database_url.replace("sqlite:///", "", 1) if settings.database_url.startswith("sqlite") else None
    if db_path:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    Base.metadata.create_all(bind=engine)
    run_migrations()
    logger.info("Database tables are ready.")

    # Start background task worker
    import threading
    from unity_check.task_worker import run_worker
    worker_thread = threading.Thread(target=run_worker, daemon=True, name="task-worker")
    worker_thread.start()
    logger.info("Background task worker started")

    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Frontend authentication
# ---------------------------------------------------------------------------


@app.post("/api/auth/login")
def frontend_login(body: dict, request: Request) -> dict:
    """Verify frontend access password.

    Rate-limited: 5 failed attempts per IP per hour.
    Returns ``{"success": true}`` on valid password.
    """
    if not settings.frontend_password:
        raise HTTPException(status_code=400, detail="系统未配置访问密码，请联系管理员在 .env 中设置 FRONTEND_PASSWORD")

    password = body.get("password", "")
    client_ip = request.client.host if request.client else "unknown"

    now = time.time()
    record = _login_attempts.get(client_ip)

    # Check if currently banned
    if record and record["banned_until"] > now:
        remaining_secs = int(record["banned_until"] - now)
        remaining_mins = max(1, remaining_secs // 60)
        raise HTTPException(
            status_code=429,
            detail=f"登录尝试过于频繁，请等待 {remaining_mins} 分钟后再试",
        )

    if password != settings.frontend_password:
        # Record failed attempt
        if record is None:
            _login_attempts[client_ip] = {"count": 1, "banned_until": 0.0}
        else:
            record["count"] += 1
            if record["count"] >= 5:
                _login_attempts[client_ip]["banned_until"] = now + 3600

        raise HTTPException(status_code=401, detail="密码错误")

    # Success: reset counter
    _login_attempts.pop(client_ip, None)
    return {"success": True}


@app.post("/webhook/github")
async def receive_github_webhook(
    request: Request,
    x_github_event: str = Header(default="", alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    db: Session = Depends(get_db),
):
    """Receive a GitHub webhook and process synchronously.

    Clones/fetches the repo, extracts the diff, runs the per-file LLM
    evaluation pipeline, and returns the result inline.
    """
    event_type = x_github_event.strip()
    payload_bytes = await request.body()

    if event_type == "ping":
        logger.info("Ping event received (delivery=%s)", x_github_delivery)
        return JSONResponse(content={"status": "ok"}, status_code=200)

    if event_type not in {"push", "pull_request"}:
        raise HTTPException(status_code=400, detail="Only push and pull_request are supported.")

    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {exc}") from exc

    # Idempotency check via delivery_id
    existed = None
    if x_github_delivery:
        existed = db.scalar(select(GithubEvent).where(GithubEvent.delivery_id == x_github_delivery))
    if existed is not None:
        return {"status": "accepted", "event_id": str(existed.id)}

    repository = ((payload.get("repository") or {}).get("full_name")) if isinstance(payload, dict) else None
    action = payload.get("action") if isinstance(payload, dict) else None
    before_sha, after_sha = extract_sha_from_payload(payload, event_type)

    # --- Repository lookup & validation ---
    repo_obj = None
    if repository:
        repo_obj = webhook_service.lookup_repository(db, repository)
        if repo_obj is None:
            raise HTTPException(
                status_code=404,
                detail=f"Repository '{repository}' is not registered. "
                       "Register it via POST /api/repositories first.",
            )
        if not repo_obj.is_active:
            raise HTTPException(status_code=403, detail=f"Repository '{repository}' is disabled.")

    # --- Signature verification ---
    if repo_obj:
        secret = _resolve_webhook_secret(repo_obj, db)
        if secret:
            if not x_hub_signature_256:
                raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
            if not webhook_service.verify_webhook_signature(
                payload_bytes, secret, x_hub_signature_256
            ):
                raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # --- Branch filtering (push only) ---
    if repo_obj and repo_obj.branch_filter and event_type == "push":
        ref = payload.get("ref", "")
        if not webhook_service.match_branch(ref, repo_obj.branch_filter):
            logger.info("Branch %s filtered out for repo %s", ref, repository)
            return {"status": "skipped", "reason": f"Branch {ref} not in filter"}

    event = GithubEvent(
        delivery_id=x_github_delivery,
        event_type=event_type,
        action=action,
        repository=repository,
        repository_id=repo_obj.id if repo_obj else None,
        before_sha=before_sha,
        after_sha=after_sha,
        payload=payload,
        status="queued",
    )
    db.add(event)
    db.flush()

    # --- Queue background task ---
    task = Task(
        type="incremental_scan",
        repository_id=repo_obj.id if repo_obj else None,
        event_id=event.id,
        progress_detail="排队中",
    )
    db.add(task)
    db.commit()
    logger.info("Queued incremental_scan task %d for event %d", task.id, event.id)
    return {"status": "accepted", "event_id": event.id, "task_id": task.id}


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


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

    base = select(GithubEvent, Repository.alias).outerjoin(
        Repository, GithubEvent.repository == Repository.name
    )
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
    rows = db.execute(
        base.order_by(order).limit(page_size).offset(offset)
    ).all()

    items = [
        {
            "id": item.id,
            "delivery_id": item.delivery_id,
            "event_type": item.event_type,
            "action": item.action,
            "repository": item.repository,
            "repository_alias": alias,
            "after_sha": item.after_sha,
            "diff_size": item.diff_size,
            "status": item.status,
            "overall_score": item.overall_score,
            "final_risk_level": item.final_risk_level,
            "recommendation": item.recommendation,
            "executive_summary": item.executive_summary,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }
        for item, alias in rows
    ]

    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


@app.get("/api/events/{event_id}")
def get_event_detail(
    event_id: int,
    include: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Return event detail, optionally including assessment.

    Query params:
        include: "assessment" to include evaluation rounds data
    """
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    # Look up repo alias
    alias = None
    if event.repository:
        repo = db.scalar(select(Repository).where(Repository.name == event.repository))
        alias = repo.alias if repo else None

    result = {
        "id": event.id,
        "delivery_id": event.delivery_id,
        "event_type": event.event_type,
        "action": event.action,
        "repository": event.repository,
        "repository_alias": alias,
        "after_sha": event.after_sha,
        "before_sha": event.before_sha,
        "clone_path": event.clone_path,
        "diff_content": event.diff_content,
        "diff_size": event.diff_size,
        "status": event.status,
        "overall_score": event.overall_score,
        "final_risk_level": event.final_risk_level,
        "recommendation": event.recommendation,
        "executive_summary": event.executive_summary,
        "dimension_a_score": event.dimension_a_score,
        "dimension_b_score": event.dimension_b_score,
        "dimension_a_summary": event.dimension_a_summary,
        "dimension_b_summary": event.dimension_b_summary,
        "error_message": event.error_message,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }

    includes = set((include or "").lower().split(","))

    if "assessment" in includes:
        rounds = db.scalars(
            select(EvaluationRound)
            .where(EvaluationRound.event_id == event_id)
            .order_by(EvaluationRound.round_number)
        ).all()
        result["assessment"] = {
            "event_id": event.id,
            "status": event.status,
            "overall_score": event.overall_score,
            "final_risk_level": event.final_risk_level,
            "recommendation": event.recommendation,
            "executive_summary": event.executive_summary,
            "dimension_a_score": event.dimension_a_score,
            "dimension_b_score": event.dimension_b_score,
            "rounds": [
                {
                    "id": r.id,
                    "round_number": r.round_number,
                    "round_type": r.round_type,
                    "file_path": r.file_path,
                    "status": r.status,
                    "score": r.score,
                    "tokens_used": r.tokens_used,
                    "duration_ms": r.duration_ms,
                    "output_data": r.output_data,
                }
                for r in rounds
            ],
            "total_tokens_used": sum(r.tokens_used or 0 for r in rounds),
            "total_duration_ms": sum(r.duration_ms or 0 for r in rounds),
        }

    return result


# ---------------------------------------------------------------------------
# Evaluation rounds
# ---------------------------------------------------------------------------


@app.get("/api/events/{event_id}/evaluations")
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
            "file_path": r.file_path,
            "status": r.status,
            "score": r.score,
            "model_name": r.model_name,
            "tokens_used": r.tokens_used,
            "duration_ms": r.duration_ms,
            "error_message": r.error_message,
            "output_data": r.output_data,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rounds
    ]


@app.get("/api/events/{event_id}/assessment")
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
        "dimension_a_score": event.dimension_a_score,
        "dimension_b_score": event.dimension_b_score,
        "rounds": [
            {
                "id": r.id,
                "round_number": r.round_number,
                "round_type": r.round_type,
                "file_path": r.file_path,
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


@app.post("/api/events/{event_id}/re-evaluate")
def re_evaluate_event(event_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete existing evaluation rounds and re-run the full pipeline synchronously."""
    event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    # Remove old evaluation rounds.
    db.query(EvaluationRound).filter(EvaluationRound.event_id == event_id).delete()
    db.flush()

    # Reset event status and evaluation fields.
    event.status = "queued"
    event.overall_score = None
    event.final_risk_level = None
    event.recommendation = None
    event.executive_summary = None
    event.dimension_a_score = None
    event.dimension_b_score = None
    event.dimension_a_summary = None
    event.dimension_b_summary = None
    db.flush()

    # Re-run evaluation pipeline synchronously.
    run_evaluation_pipeline(event, db)
    db.commit()

    return {
        "status": "success",
        "event_id": event.id,
        "message": "Re-evaluation completed.",
    }


# ---------------------------------------------------------------------------
# Repository management
# ---------------------------------------------------------------------------


def _repo_to_dict(repo: Repository) -> dict:
    """Serialize a Repository to a safe API response dict (no secret value exposed)."""
    # webhook_secret_type: "custom" | "none" | "generic"
    if repo.webhook_secret == "":
        secret_type = "none"
    elif repo.webhook_secret is not None:
        secret_type = "custom"
    else:
        secret_type = "generic"

    return {
        "id": repo.id,
        "name": repo.name,
        "alias": repo.alias,
        "clone_url": repo.clone_url,
        "webhook_secret_type": secret_type,
        "ssh_key_path": repo.ssh_key_path,
        "branch_filter": repo.branch_filter,
        "is_active": repo.is_active,
        "status": repo.status,
        "last_synced_at": repo.last_synced_at.isoformat() if repo.last_synced_at else None,
        "local_path": repo.local_path,
        "error_message": repo.error_message,
        "created_at": repo.created_at.isoformat() if repo.created_at else None,
        "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
    }


@app.get("/api/repositories")
def list_repositories_api(
    is_active: bool | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    """List all registered repositories."""
    repos = repository_service.list_repositories(db, is_active=is_active)
    return [_repo_to_dict(r) for r in repos]


@app.post("/api/repositories", status_code=201)
def create_repository_api(
    body: RepositoryCreate,
    scan: bool = Query(False, description="Trigger a full initial scan after registration"),
    db: Session = Depends(get_db),
) -> dict:
    """Register a new repository.

    When ``?scan=true`` is set and *clone_url* is provided, the repository
    is cloned and a full ``initial_scan`` event is triggered immediately.
    """
    try:
        repo = repository_service.create_repository(
            db,
            name=body.name,
            alias=body.alias,
            clone_url=body.clone_url,
            webhook_secret=body.webhook_secret,
            ssh_key_path=body.ssh_key_path,
            branch_filter=body.branch_filter,
            is_active=body.is_active if body.is_active is not None else True,
        )
        result = _repo_to_dict(repo)

        # Optional: queue full scan after registration
        if scan and repo.clone_url:
            task = Task(
                type="full_scan",
                repository_id=repo.id,
                progress_detail="排队中",
            )
            db.add(task)
            db.flush()
            result["task"] = {
                "id": task.id,
                "status": "pending",
                "progress_detail": "排队中",
            }

        db.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/repositories/{repo_id}")
def get_repository_detail(repo_id: int, db: Session = Depends(get_db)) -> dict:
    """Get repository details by id."""
    repo = repository_service.get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found")
    return _repo_to_dict(repo)


@app.put("/api/repositories/{repo_id}")
def update_repository_api(
    repo_id: int,
    body: RepositoryUpdate,
    db: Session = Depends(get_db),
) -> dict:
    """Update repository configuration."""
    try:
        repo = repository_service.update_repository(
            db, repo_id, **body.model_dump(exclude_unset=True)
        )
        if repo is None:
            raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found")
        db.commit()
        return _repo_to_dict(repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/repositories/{repo_id}/scan")
def scan_repository(
    repo_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Trigger a full scan of all tracked .cs files in the repository.

    Clones / fetches the bare repo, resolves the default branch HEAD,
    generates a unified diff of all ``.cs`` files (as new additions against
    ``/dev/null``), creates an ``initial_scan`` event, and runs the full
    evaluation pipeline on every file.

    This can be triggered at any time, including right after registration
    to establish a baseline assessment of existing code.

    If the bare repo already exists (previously cloned) but ``git fetch``
    fails (e.g. network issue or expired credentials), the endpoint will
    fall back to scanning the already-fetched data rather than failing.
    """
    repo = repository_service.get_repository(db, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found")

    if not repo.clone_url:
        raise HTTPException(
            status_code=400,
            detail="Repository has no clone_url configured — set one via PUT /api/repositories/{id}",
        )

    # Guard: reject if a task for this repo is already pending or processing
    existing = db.scalar(
        select(Task)
        .where(Task.repository_id == repo_id, Task.status.in_(["pending", "processing"]))
        .limit(1)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"该仓库已有扫描任务正在进行中（任务 #{existing.id}，状态：{existing.status}）",
        )

    # Queue background scan task
    task = Task(
        type="full_scan",
        repository_id=repo.id,
        progress_detail="排队中",
    )
    db.add(task)
    db.commit()
    logger.info("Queued full_scan task %d for repo %s", task.id, repo.name)
    return {
        "status": "accepted",
        "task_id": task.id,
        "task": {
            "id": task.id,
            "type": "full_scan",
            "status": "pending",
            "progress_detail": "排队中",
        },
    }


@app.delete("/api/repositories/{repo_id}")
def delete_repository_api(repo_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a repository (events kept, FK set NULL)."""
    ok = repository_service.delete_repository(db, repo_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Repository {repo_id} not found")
    db.commit()
    return {"status": "deleted", "id": repo_id}


# ---------------------------------------------------------------------------
# Task management
# ---------------------------------------------------------------------------


@app.get("/api/tasks")
def list_tasks(
    repository_id: int | None = None,
    status: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> list[dict]:
    """List background tasks, most recent first."""
    base = select(Task)
    if repository_id:
        base = base.where(Task.repository_id == repository_id)
    if status:
        base = base.where(Task.status == status)
    base = base.order_by(Task.created_at.desc()).limit(min(limit, 100))
    return [
        {
            "id": t.id,
            "type": t.type,
            "status": t.status,
            "progress_detail": t.progress_detail,
            "progress_value": t.progress_value,
            "repository_id": t.repository_id,
            "event_id": t.event_id,
            "error_message": t.error_message,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        }
        for t in db.scalars(base).all()
    ]


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)) -> dict:
    """Get a single task's status and progress."""
    task = db.scalar(select(Task).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {
        "id": task.id,
        "type": task.type,
        "status": task.status,
        "progress_detail": task.progress_detail,
        "progress_value": task.progress_value,
        "repository_id": task.repository_id,
        "event_id": task.event_id,
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Unified Dashboard / Stats API
# ---------------------------------------------------------------------------


@app.get("/api/dashboard")
def unified_dashboard(
    section: str = "summary",
    days: int = 30,
    repository: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Unified dashboard endpoint — one entry point for all dashboard/stats data.

    Query params:
        section: "summary" (default), "trends", "distribution", "scores", "hotspots"
        days: lookback days (default 30, max 365)
        repository: filter by repository name
        from_date, to_date: ISO-format date range (for scores section)
        limit: max items for hotspots (default 10, max 50)
    """
    from datetime import datetime, timedelta, timezone

    section = section.lower().strip()

    if section == "summary":
        return _dashboard_summary(days, repository, db)
    elif section == "trends":
        return _dashboard_trends(days, repository, db)
    elif section == "distribution":
        return _dashboard_issue_distribution(days, repository, db)
    elif section == "scores":
        return _stats_scores(from_date, to_date, repository, db)
    elif section == "hotspots":
        return _stats_hotspots(limit, days, repository, db)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown section '{section}'. Valid: summary, trends, distribution, scores, hotspots",
        )


# ---------------------------------------------------------------------------
# Dashboard internal helpers
# ---------------------------------------------------------------------------


def _dashboard_summary(days: int, repository: str | None, db: Session) -> dict:
    """Aggregated dashboard summary: event counts, risk distribution, score stats."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    base = select(GithubEvent).where(GithubEvent.created_at >= since)
    if repository:
        base = base.where(GithubEvent.repository == repository)

    events = db.scalars(base.order_by(GithubEvent.created_at.desc())).all()

    # Build repo alias lookup cache
    all_repos = db.scalars(select(Repository)).all()
    alias_map: dict[str, str | None] = {}
    for r in all_repos:
        if r.alias:
            alias_map[r.name] = r.alias

    total = len(events)
    risk_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    scores: list[float] = []

    for e in events:
        risk = (e.final_risk_level or "unknown").lower()
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        t = e.event_type or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1
        if e.overall_score is not None:
            scores.append(e.overall_score)

    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    recent = [
        {
            "id": e.id,
            "event_type": e.event_type,
            "repository": e.repository,
            "repository_alias": alias_map.get(e.repository) if e.repository else None,
            "status": e.status,
            "overall_score": e.overall_score,
            "final_risk_level": e.final_risk_level,
            "recommendation": e.recommendation,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events[:10]
    ]

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


def _dashboard_trends(days: int, repository: str | None, db: Session) -> list[dict]:
    """Daily trend data: score & event count per day."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    base = select(GithubEvent).where(GithubEvent.created_at >= since)
    if repository:
        base = base.where(GithubEvent.repository == repository)

    events = db.scalars(base.order_by(GithubEvent.created_at)).all()

    daily: dict[str, dict] = {}
    for e in events:
        day = e.created_at.strftime("%Y-%m-%d") if e.created_at else "unknown"
        if day not in daily:
            daily[day] = {"date": day, "count": 0, "scores": []}
        daily[day]["count"] += 1
        if e.overall_score is not None:
            daily[day]["scores"].append(e.overall_score)

    result = []
    for day, data in sorted(daily.items()):
        sl = data["scores"]
        result.append({
            "date": day,
            "event_count": data["count"],
            "avg_score": round(sum(sl) / len(sl), 1) if sl else None,
        })
    return result


def _dashboard_issue_distribution(days: int, repository: str | None, db: Session) -> dict:
    """Semantic finding distribution: by category, severity, source."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))

    # Semantic findings from evaluation_rounds (dimension rounds)
    eval_base = (
        select(EvaluationRound)
        .join(GithubEvent)
        .where(
            GithubEvent.created_at >= since,
            EvaluationRound.round_type.in_(
                ["functionality_best_practices", "security_performance_health"]
            ),
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
        "semantic": {
            "total": sum(semantic_category_counts.values()),
            "by_category": semantic_category_counts,
            "by_severity": semantic_severity_counts,
        },
        "since_days": days,
    }


def _stats_scores(
    from_date: str | None, to_date: str | None,
    repository: str | None, db: Session,
) -> dict:
    """Score statistics over a date range."""
    from datetime import datetime, timezone

    base = select(GithubEvent.overall_score, GithubEvent.final_risk_level, GithubEvent.created_at)
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


def _stats_hotspots(limit: int, days: int, repository: str | None, db: Session) -> list[dict]:
    """Top files by evaluation round count (hotspots)."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    base = (
        select(EvaluationRound.file_path, func.count().label("cnt"))
        .join(GithubEvent)
        .where(
            GithubEvent.created_at >= since,
            EvaluationRound.file_path.isnot(None),
        )
    )
    if repository:
        base = base.where(GithubEvent.repository == repository)
    base = (
        base.group_by(EvaluationRound.file_path)
        .order_by(func.count().desc())
        .limit(min(limit, 50))
    )
    rows = db.execute(base).all()
    return [{"file": r[0], "count": r[1]} for r in rows]


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@app.post("/api/system/reset")
def reset_system(db: Session = Depends(get_db)) -> dict:
    """Reset the entire system to initial state.

    Clears all data: evaluation rounds, events, tasks, repositories,
    and deletes all cloned bare repos from disk. Schema is preserved.
    """
    import shutil

    logger.warning("System reset requested — clearing all data")

    # 1. Delete evaluation rounds (cascade-safe: direct delete)
    db.query(EvaluationRound).delete()
    db.flush()

    # 2. Delete all events
    db.query(GithubEvent).delete()
    db.flush()

    # 3. Delete all tasks
    db.query(Task).delete()
    db.flush()

    # 4. Delete all repositories
    db.query(Repository).delete()
    db.flush()

    db.commit()
    logger.info("All database records cleared.")

    # 5. Delete all cloned bare repos from disk
    clone_base = os.path.abspath(settings.git_clone_base_dir)
    if os.path.isdir(clone_base):
        for entry in os.listdir(clone_base):
            entry_path = os.path.join(clone_base, entry)
            try:
                if os.path.isdir(entry_path):
                    shutil.rmtree(entry_path)
                    logger.info("Deleted repo directory: %s", entry_path)
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", entry_path, exc)

    logger.warning("System reset completed.")
    return {"status": "ok", "message": "系统已重置，所有数据已清除"}


# ---------------------------------------------------------------------------
# System settings (global config)
# ---------------------------------------------------------------------------


@app.get("/api/system/settings")
def get_system_settings(db: Session = Depends(get_db)) -> dict:
    """Get global system settings.

    Returns runtime values (DB override if set, otherwise ``.env`` fallback).
    The actual webhook secret is **not** returned — only whether it is set.
    """
    setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "generic_webhook_secret"))
    effective_secret = setting.value if (setting and setting.value) else settings.generic_webhook_secret

    ssh_setting = db.scalar(select(SystemSetting).where(SystemSetting.key == "generic_ssh_key_path"))
    effective_ssh = ssh_setting.value if (ssh_setting and ssh_setting.value) else (settings.git_ssh_key_path or "")

    return {
        "generic_webhook_secret_set": bool(effective_secret),
        "generic_ssh_key_path": effective_ssh,
    }


@app.put("/api/system/settings")
def update_system_settings(body: dict, db: Session = Depends(get_db)) -> dict:
    """Update global system settings.

    Accepted keys: ``generic_webhook_secret``, ``generic_ssh_key_path``.
    Send ``""`` (empty string) to clear a value and fall back to ``.env``.
    """
    allowed_keys = {"generic_webhook_secret", "generic_ssh_key_path"}

    for key, value in body.items():
        if key not in allowed_keys:
            continue
        if not value:
            # Clear DB override → fallback to .env
            existing = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
            if existing:
                db.delete(existing)
        else:
            setting = db.scalar(select(SystemSetting).where(SystemSetting.key == key))
            if setting:
                setting.value = value
            else:
                db.add(SystemSetting(key=key, value=value))

    db.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Production SPA fallback (MUST be last — catches unmatched paths only)
# ---------------------------------------------------------------------------
if settings.app_env == "production":
    frontend_dist = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        settings.frontend_dist_dir.lstrip("./"),
    )
    if os.path.isdir(frontend_dist):
        dist_index = os.path.join(frontend_dist, "index.html")
        logger.info("Frontend dist found at %s; enabling SPA fallback", frontend_dist)

        @app.get("/{full_path:path}")
        async def serve_frontend(full_path: str) -> FileResponse:
            """Serve static assets or fall back to index.html for SPA routing.

            Only triggers when no explicit API route matches first.
            """
            file_path = os.path.join(frontend_dist, full_path)
            if os.path.isfile(file_path):
                return FileResponse(file_path)
            return FileResponse(dist_index)

    else:
        logger.warning("frontend_dist_dir %s not found; frontend not served", frontend_dist)
