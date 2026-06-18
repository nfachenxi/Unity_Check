from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from unity_check import repository_service
from unity_check.config import get_settings
from unity_check.db import SessionLocal
from unity_check.git_service import (
    GitServiceError,
    ensure_bare_repo,
    extract_clone_url_from_payload,
    generate_full_cs_diff,
    get_default_branch_head,
    get_diff,
    resolve_bare_path,
)
from unity_check.models import GithubEvent, Repository, Task
from unity_check.orchestrator import run_evaluation_pipeline

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------


def _recover_stale_tasks() -> None:
    """Mark tasks stuck in 'processing' beyond timeout as 'failed'.

    Handles the case where the server was killed mid-task.
    """
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.task_processing_timeout)
        stmt = (
            update(Task)
            .where(Task.status == "processing")
            .where(Task.updated_at < cutoff)
            .values(
                status="failed",
                error_message="任务超时（进程重启）",
                progress_detail="超时",
            )
        )
        result = db.execute(stmt)
        db.commit()
        if result.rowcount:
            logger.info("Recovered %d stale task(s) on startup", result.rowcount)
    except Exception:
        logger.exception("Failed to recover stale tasks")
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_task_progress(db, task_pk: int, progress: str) -> None:
    """Update a task's progress_detail and commit."""
    db.execute(update(Task).where(Task.id == task_pk).values(progress_detail=progress))
    db.commit()


def _fail_task(db, task_pk: int, message: str) -> None:
    """Mark a task as failed."""
    db.execute(
        update(Task)
        .where(Task.id == task_pk)
        .values(status="failed", progress_detail="失败", error_message=str(message)[:200])
    )
    db.commit()


# ---------------------------------------------------------------------------
# Handler: full_scan
# ---------------------------------------------------------------------------


def _process_full_scan(task: Task) -> None:
    """Full scan: clone/fetch bare repo → full diff → evaluation pipeline.

    Used for both initial scan-on-create and manual scan.
    """
    logger.info("Starting full_scan task %d for repo %s", task.id, task.repository_id)
    db = SessionLocal()
    try:
        pk = task.id
        _update_task_progress(db, pk, "正在克隆仓库...")

        repo = db.scalar(select(Repository).where(Repository.id == task.repository_id))
        if repo is None:
            _fail_task(db, pk, "仓库不存在")
            return

        # --- Clone / fetch bare repo ---
        try:
            bare_path = ensure_bare_repo(repo.clone_url, ssh_key_path=repo.ssh_key_path)
        except GitServiceError:
            _detect = resolve_bare_path(repo.clone_url) if repo.clone_url else None
            if _detect and os.path.isdir(_detect):
                logger.warning("Fetch failed for %s, using existing bare repo", repo.name)
                bare_path = _detect
            else:
                raise

        _update_task_progress(db, pk, "正在解析代码文件...")

        # --- Resolve HEAD ---
        sha = get_default_branch_head(bare_path)
        if not sha:
            _fail_task(db, pk, "无法解析默认分支 HEAD")
            return

        # --- Generate full diff ---
        diff = generate_full_cs_diff(bare_path, sha)

        # --- Create GithubEvent ---
        event = GithubEvent(
            delivery_id=None,
            event_type="initial_scan",
            action="scan",
            repository=repo.name,
            repository_id=repo.id,
            after_sha=sha,
            clone_path=bare_path,
            diff_content=diff,
            diff_size=len(diff.encode("utf-8")) if diff else 0,
            payload={
                "repository": {"full_name": repo.name},
                "scan_type": "full",
                "commit_sha": sha,
            },
            status="running",
        )
        db.add(event)
        db.flush()

        # Link task to event
        db.execute(update(Task).where(Task.id == pk).values(event_id=event.id))
        db.commit()

        # --- Run evaluation ---
        _update_task_progress(db, pk, "正在评估代码...")
        run_evaluation_pipeline(event, db)

        # --- Success ---
        repository_service.mark_repository_synced(db, repo.id)
        db.execute(
            update(Task)
            .where(Task.id == pk)
            .values(status="completed", progress_detail="扫描完成")
        )
        db.commit()
        logger.info("Full_scan task %d completed (event %d)", task.id, event.id)

    except Exception as exc:
        logger.exception("Full_scan task %d failed", task.id)
        try:
            _fail_task(db, pk, str(exc))
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Handler: incremental_scan (webhook-triggered)
# ---------------------------------------------------------------------------


def _process_incremental_scan(task: Task) -> None:
    """Incremental scan: fetch → delta diff → evaluation pipeline."""
    logger.info("Starting incremental_scan task %d for event %s", task.id, task.event_id)
    db = SessionLocal()
    event: GithubEvent | None = None
    repo: Repository | None = None
    try:
        pk = task.id
        _update_task_progress(db, pk, "正在拉取仓库更新...")

        event = db.scalar(select(GithubEvent).where(GithubEvent.id == task.event_id))
        if event is None:
            _fail_task(db, pk, "事件不存在")
            return

        if task.repository_id:
            repo = db.scalar(select(Repository).where(Repository.id == task.repository_id))

        # --- Git operations ---
        clone_url = extract_clone_url_from_payload(event.payload)
        try:
            if clone_url and event.after_sha:
                bare_path = ensure_bare_repo(clone_url, ssh_key_path=repo.ssh_key_path if repo else None)
                event.clone_path = bare_path

                _update_task_progress(db, pk, "正在解析代码差异...")

                diff = get_diff(bare_path, event.before_sha or "", event.after_sha)
                event.diff_content = diff
                event.diff_size = len(diff.encode("utf-8")) if diff else 0

        except GitServiceError:
            # Fallback: if bare repo exists on disk, use existing data
            if clone_url:
                _detect = resolve_bare_path(clone_url)
                if _detect and os.path.isdir(_detect):
                    logger.warning("Fetch failed, using existing bare repo for event %s", event.id)
                    event.clone_path = _detect
                    diff = get_diff(_detect, event.before_sha or "", event.after_sha)
                    event.diff_content = diff
                    event.diff_size = len(diff.encode("utf-8")) if diff else 0
                else:
                    raise
            else:
                raise

        # --- Run evaluation ---
        event.status = "running"
        db.flush()

        _update_task_progress(db, pk, "正在评估代码...")
        run_evaluation_pipeline(event, db)

        # --- Success ---
        if repo:
            repository_service.mark_repository_synced(db, repo.id)
        db.execute(
            update(Task)
            .where(Task.id == pk)
            .values(status="completed", progress_detail="扫描完成")
        )
        db.commit()
        logger.info("Incremental_scan task %d completed (event %d)", task.id, event.id)

    except Exception as exc:
        logger.exception("Incremental_scan task %d failed", task.id)
        try:
            _fail_task(db, task.id, str(exc))
            # Also mark the GithubEvent as failed
            if event and event.id:
                db.execute(
                    update(GithubEvent)
                    .where(GithubEvent.id == event.id)
                    .values(status="failed", error_message=str(exc)[:200])
                )
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Worker main loop
# ---------------------------------------------------------------------------


def run_worker() -> None:
    """Main worker loop — runs in a background daemon thread.

    Polls for ``pending`` tasks, processes them one at a time globally,
    and updates progress / status throughout.
    """
    logger.info(
        "Task worker started (interval=%ds, timeout=%ds)",
        settings.task_worker_interval,
        settings.task_processing_timeout,
    )
    _recover_stale_tasks()

    while True:
        db = SessionLocal()
        try:
            task = db.scalar(
                select(Task)
                .where(Task.status == "pending")
                .order_by(Task.created_at.asc())
                .limit(1)
            )

            if task is not None:
                # Atomically claim the task
                task.status = "processing"
                db.commit()
                db.close()

                # Process in its own session
                if task.type == "full_scan":
                    _process_full_scan(task)
                elif task.type == "incremental_scan":
                    _process_incremental_scan(task)
                else:
                    logger.warning("Unknown task type: %s", task.type)
                    db2 = SessionLocal()
                    try:
                        _fail_task(db2, task.id, f"Unknown type: {task.type}")
                    finally:
                        db2.close()
            else:
                db.close()

        except Exception:
            logger.exception("Task worker loop error")
            try:
                db.close()
            except Exception:
                pass

        time.sleep(settings.task_worker_interval)
