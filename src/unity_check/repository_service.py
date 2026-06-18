"""CRUD operations for Repository records."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from unity_check.models import Repository

logger = logging.getLogger(__name__)

_ALLOWED_UPDATE_FIELDS = {
    "alias",
    "clone_url",
    "webhook_secret",
    "ssh_key_path",
    "branch_filter",
    "is_active",
    "status",
    "local_path",
    "error_message",
    "last_synced_at",
}


def create_repository(
    db: Session,
    name: str,
    alias: str | None = None,
    clone_url: str | None = None,
    webhook_secret: str | None = None,
    ssh_key_path: str | None = None,
    branch_filter: str | None = None,
    is_active: bool = True,
) -> Repository:
    """Create a new repository record."""
    if not name:
        raise ValueError("name is required")
    existing = db.scalar(select(Repository).where(Repository.name == name))
    if existing:
        raise ValueError(f"Repository '{name}' already exists")

    repo = Repository(
        name=name,
        alias=alias,
        clone_url=clone_url,
        webhook_secret=webhook_secret,
        ssh_key_path=ssh_key_path,
        branch_filter=branch_filter,
        is_active=is_active,
        status="active",
    )
    db.add(repo)
    db.flush()
    logger.info("Created repository: %s (id=%s)", name, repo.id)
    return repo


def get_repository(db: Session, repo_id: int) -> Repository | None:
    """Get a repository by id."""
    return db.scalar(select(Repository).where(Repository.id == repo_id))


def get_repository_by_name(db: Session, name: str) -> Repository | None:
    """Get a repository by full name (owner/repo)."""
    return db.scalar(select(Repository).where(Repository.name == name))


def list_repositories(db: Session, is_active: bool | None = None) -> list[Repository]:
    """List all repositories, optionally filtered by active status."""
    base = select(Repository).order_by(Repository.created_at.desc())
    if is_active is not None:
        base = base.where(Repository.is_active == is_active)
    return list(db.scalars(base).all())


def update_repository(db: Session, repo_id: int, **kwargs: Any) -> Repository | None:
    """Update allowed fields on a repository.

    Only the following fields may be updated: clone_url, webhook_secret,
    ssh_key_path, branch_filter, is_active, status, local_path,
    error_message, last_synced_at.
    """
    repo = get_repository(db, repo_id)
    if repo is None:
        return None

    for key, value in kwargs.items():
        if key not in _ALLOWED_UPDATE_FIELDS:
            raise ValueError(f"Field '{key}' is not allowed for update")
        setattr(repo, key, value)

    db.flush()
    logger.info("Updated repository %s (id=%s)", repo.name, repo.id)
    return repo


def delete_repository(db: Session, repo_id: int) -> bool:
    """Delete a repository record.

    Associated events are kept with repository_id set to NULL
    (via ondelete='SET NULL').
    """
    repo = get_repository(db, repo_id)
    if repo is None:
        return False

    db.delete(repo)
    db.flush()
    logger.info("Deleted repository %s (id=%s)", repo.name, repo.id)
    return True


def mark_repository_synced(db: Session, repo_id: int) -> Repository | None:
    """Mark a repository as successfully synced."""
    repo = get_repository(db, repo_id)
    if repo is None:
        return None
    repo.status = "active"
    repo.last_synced_at = datetime.now(timezone.utc)
    repo.error_message = None
    db.flush()
    return repo


def mark_repository_error(db: Session, repo_id: int, error_message: str) -> Repository | None:
    """Mark a repository as errored after a failed sync."""
    repo = get_repository(db, repo_id)
    if repo is None:
        return None
    repo.status = "error"
    repo.error_message = error_message
    db.flush()
    return repo
