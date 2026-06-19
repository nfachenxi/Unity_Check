from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from unity_check.config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args, pool_pre_ping=True)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        """Enable WAL mode and set busy timeout for better concurrency."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """Run lightweight schema migrations for existing tables.

    SQLAlchemy's ``create_all`` only creates tables that do not yet exist;
    it never alters existing tables. This function handles additive column
    changes that ``create_all`` cannot perform.
    """
    inspector = inspect(engine)

    # --- migration: add repository_id to github_events ---
    gh_cols = {c["name"] for c in inspector.get_columns("github_events")}
    if "repository_id" not in gh_cols:
        logger.info("Migration: adding repository_id column to github_events")
        with engine.connect() as conn:
            conn.execute(
                text(
                    "ALTER TABLE github_events "
                    "ADD COLUMN repository_id INTEGER REFERENCES repositories(id) ON DELETE SET NULL"
                )
            )
            conn.commit()

    # (future migrations go here)

    # --- migration: add alias to repositories ---
    repo_cols = {c["name"] for c in inspector.get_columns("repositories")}
    if "alias" not in repo_cols:
        logger.info("Migration: adding alias column to repositories")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE repositories ADD COLUMN alias VARCHAR(255)"))
            conn.commit()

    # --- migration: add progress_value to tasks ---
    task_cols = {c["name"] for c in inspector.get_columns("tasks")}
    if "progress_value" not in task_cols:
        logger.info("Migration: adding progress_value column to tasks")
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN progress_value INTEGER"))
            conn.commit()

    # --- migration: ensure system_settings table ---
    Base.metadata.create_all(bind=engine)
