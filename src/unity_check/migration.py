"""Database migration script for Unity Check v2 schema changes.

Idempotent — safe to run multiple times. Uses raw SQL for column operations
since SQLAlchemy's batch mode adds complexity for a one-time migration.

Usage:
    python -c "from unity_check.migration import run_migration; run_migration()"
"""

from __future__ import annotations

import logging

from unity_check.db import engine

logger = logging.getLogger(__name__)

MIGRATION_SQL = [
    # GithubEvent: add new dimension columns (PostgreSQL)
    "ALTER TABLE github_events ADD COLUMN IF NOT EXISTS dimension_a_score DOUBLE PRECISION",
    "ALTER TABLE github_events ADD COLUMN IF NOT EXISTS dimension_b_score DOUBLE PRECISION",
    "ALTER TABLE github_events ADD COLUMN IF NOT EXISTS dimension_a_summary TEXT",
    "ALTER TABLE github_events ADD COLUMN IF NOT EXISTS dimension_b_summary TEXT",
    # GithubEvent: drop legacy backward-compat columns
    "ALTER TABLE github_events DROP COLUMN IF EXISTS risk_level",
    "ALTER TABLE github_events DROP COLUMN IF EXISTS evaluation_summary",
    # EvaluationRound: add file_path column
    "ALTER TABLE evaluation_rounds ADD COLUMN IF NOT EXISTS file_path VARCHAR(1024)",
]


def run_migration() -> None:
    """Execute all migration SQL statements against the configured database.

    Each statement uses IF EXISTS / IF NOT EXISTS so the function is idempotent.
    """
    with engine.connect() as conn:
        for stmt in MIGRATION_SQL:
            logger.info("Running: %s", stmt)
            conn.execute(__import__("sqlalchemy").text(stmt))
        conn.commit()
    logger.info("Migration complete: %d statements executed.", len(MIGRATION_SQL))
