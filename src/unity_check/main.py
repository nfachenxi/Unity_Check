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
from unity_check.models import GithubEvent
from unity_check.tasks import process_github_event

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
