"""Roslyn rule-analysis service: HTTP client, diff parsing, baseline scans.

Responsibilities
----------------
1. Determine which *paths* a repository should analyse (DB config → .env fallback).
2. Extract changed ``.cs`` file paths from a git diff.
3. Filter paths against the allowed directory prefixes.
4. Call the Roslyn Docker sidecar ``POST /analyze``.
5. Parse the structured response into ``RuleResult`` ORM rows.
6. Orchestrate *baseline scans* (first-time full-repo scan, batched).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from unity_check.config import get_settings
from unity_check.models import RepoScanConfig, RuleResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
BASELINE_BATCH_SIZE = 30  # files per Roslyn request during baseline scans
ROSLYN_HEALTH_TIMEOUT = 5.0  # seconds
ROSLYN_ANALYZE_TIMEOUT = 120.0  # seconds; large batches may take time
CS_EXTENSION = ".cs"

# ---------------------------------------------------------------------------
# 1. Analyse-path resolution (DB → .env fallback)
# ---------------------------------------------------------------------------


def get_analyze_paths(repository: str, db: Session) -> list[str]:
    """Return the list of *prefix* directories to scan for *repository*.

    Priority:
    1. ``RepoScanConfig.analyze_paths`` (set via API).
    2. ``DEFAULT_ANALYZE_PATHS`` from ``.env`` / env vars.
    """
    config = db.scalar(
        select(RepoScanConfig).where(RepoScanConfig.repository == repository)
    )
    if config and config.analyze_paths:
        return _normalize_paths(config.analyze_paths)

    settings = get_settings()
    raw = settings.default_analyze_paths
    return _normalize_paths([p.strip() for p in raw.split(",") if p.strip()])


def _normalize_paths(paths: list[str]) -> list[str]:
    """Strip trailing slashes and backslashes; return only non-empty entries."""
    result: list[str] = []
    for p in paths:
        p = p.strip().rstrip("/\\")
        if p:
            result.append(p)
    return result


# ---------------------------------------------------------------------------
# 2. Diff → changed .cs files
# ---------------------------------------------------------------------------

# Matches ``diff --git a/<path> b/<path>`` lines in unified diff output.
_DIFF_FILE_RE = re.compile(r"^diff\s+--git\s+a/(.+?)\s+b/(.+?)$", re.MULTILINE)


def extract_cs_files_from_diff(diff_content: str) -> list[str]:
    """Parse a unified diff and return *unique* ``.cs`` file paths.

    Only files whose path ends with ``.cs`` (case‑insensitive) are returned.
    """
    if not diff_content:
        return []

    paths: list[str] = []
    for match in _DIFF_FILE_RE.finditer(diff_content):
        path = match.group(2)  # b/ path (new file side)
        if path.lower().endswith(CS_EXTENSION):
            paths.append(path)
    return _deduplicate_keep_order(paths)


def _deduplicate_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# 3. Path filtering
# ---------------------------------------------------------------------------


def filter_analyze_targets(
    file_paths: list[str], analyze_paths: list[str]
) -> list[str]:
    """Keep only *file_paths* that are underneath one of *analyze_paths*.

    Matching is prefix‑based and normalised to forward slashes.
    """
    if not analyze_paths:
        return []

    result: list[str] = []
    for fp in file_paths:
        norm = fp.replace("\\", "/")
        for prefix in analyze_paths:
            p_norm = prefix.replace("\\", "/")
            if norm.startswith(p_norm + "/") or norm == p_norm:
                result.append(fp)
                break
    return result


# ---------------------------------------------------------------------------
# 4. Roslyn HTTP client
# ---------------------------------------------------------------------------


def _roslyn_client() -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=settings.roslyn_service_url.rstrip("/"),
        timeout=ROSLYN_ANALYZE_TIMEOUT,
    )


def roslyn_health_check() -> bool:
    """Return ``True`` when the Roslyn service responds to ``GET /health``."""
    try:
        with _roslyn_client() as client:
            resp = client.get("/health", timeout=ROSLYN_HEALTH_TIMEOUT)
            return resp.status_code == 200
    except httpx.RequestError:
        return False


def run_roslyn_analysis(
    files: list[tuple[str, str]],  # [(path, content), ...]
) -> list[dict]:
    """Send one or more source files to the Roslyn service for analysis.

    Parameters
    ----------
    files : list[tuple[str, str]]
        Each element is ``(relative_path, source_code)``.

    Returns
    -------
    list[dict]
        Deserialised ``diagnostics`` list from the Roslyn JSON response.
        Returns an empty list on any transport or decode error.
    """
    if not files:
        return []

    payload: dict = {
        "files": [{"path": p, "content": c} for p, c in files],
    }

    try:
        with _roslyn_client() as client:
            resp = client.post("/analyze", json=payload)
            resp.raise_for_status()
            data: dict = resp.json()
    except httpx.RequestError as exc:
        logger.warning("Roslyn HTTP request failed: %s", exc)
        return []
    except ValueError as exc:
        logger.warning("Roslyn response was not valid JSON: %s", exc)
        return []

    diagnostics: list[dict] = data.get("diagnostics", [])
    return diagnostics


# ---------------------------------------------------------------------------
# 5. Result persistence
# ---------------------------------------------------------------------------


def save_rule_results(
    db: Session,
    event_id: int,
    diagnostics: list[dict],
    scan_type: str,
) -> int:
    """Persist Roslyn diagnostics as ``RuleResult`` rows.

    Existing results for the same *event_id* + *scan_type* are deleted first
    so re-delivery / re-analysis is idempotent.

    Returns the number of rows inserted.
    """
    # Idempotent: remove previous results for this event + scan_type.
    db.query(RuleResult).filter(
        RuleResult.event_id == event_id,
        RuleResult.scan_type == scan_type,
    ).delete()

    rows: list[RuleResult] = []
    for d in diagnostics:
        severity_raw = str(d.get("severity", "Warning"))
        rows.append(
            RuleResult(
                event_id=event_id,
                rule_id=d.get("id", "unknown")[:32],
                rule_name=d.get("title", d.get("id", "unknown"))[:128],
                file_path=d.get("filePath", d.get("file_path", ""))[:1024],
                line_number=d.get("startLine") or d.get("start_line"),
                column_number=d.get("startColumn") or d.get("start_column"),
                severity=severity_raw,
                category=(d.get("category") or "")[:64],
                message=d.get("message", ""),
                snippet=(d.get("snippet") or "")[:4096],
                scan_type=scan_type,
            )
        )

    if rows:
        db.add_all(rows)
        db.flush()

    count = len(rows)
    logger.info(
        "Saved %d rule_results for event_id=%d scan_type=%s",
        count,
        event_id,
        scan_type,
    )
    return count


# ---------------------------------------------------------------------------
# 6. Baseline scan (first-time full repository analysis)
# ---------------------------------------------------------------------------

_CS_GLOB = f"**/*{CS_EXTENSION}"


def _collect_cs_files(root_dir: str, analyze_paths: list[str]) -> list[str]:
    """Walk *root_dir* and return ``.cs`` files under *analyze_paths*."""
    result: list[str] = []
    base = Path(root_dir)

    # Resolve repo-relative glob for each configured path.
    # We generate an explicit file list so we can report progress.
    for ap in analyze_paths:
        scan_root = base / ap
        if not scan_root.is_dir():
            logger.debug("Analyze path not found in repo: %s", scan_root)
            continue
        for cs_file in scan_root.rglob(f"*{CS_EXTENSION}"):
            # Skip hidden / unity-package-cache / plugin directories
            parts = cs_file.relative_to(base).parts
            if any(p.startswith(".") for p in parts):
                continue
            if "Plugins" in parts or "ThirdParty" in parts:
                # Common Unity convention: third-party code is under Plugins/
                # or ThirdParty/.  These are skipped by default.
                continue
            result.append(str(cs_file.relative_to(base)))

    return sorted(result)


def run_baseline_scan(
    repository: str,
    repo_path: str,  # absolute local path to checked-out repo
    db: Session,
) -> dict:
    """Execute a full baseline scan for *repository*.

    This is called asynchronously (Celery task) and should NOT be invoked
    inside the hot webhook path synchronously.

    Returns a summary dict.
    """
    analyze_paths = get_analyze_paths(repository, db)
    if not analyze_paths:
        logger.warning("No analyze_paths configured for %s – skipping baseline", repository)
        return {"status": "skipped", "reason": "no analyze_paths configured"}

    all_cs_files = _collect_cs_files(repo_path, analyze_paths)
    if not all_cs_files:
        # Update config so we don't keep retrying an empty repo.
        _update_scan_config(
            db, repository, baseline_scan_status="done",
            is_baseline_scanned=True, total_files=0, total_issues=0,
        )
        return {"status": "done", "total_files": 0, "total_issues": 0}

    _update_scan_config(
        db, repository, baseline_scan_status="running",
        total_files=len(all_cs_files),
    )

    total_issues = 0
    batches = [
        all_cs_files[i : i + BASELINE_BATCH_SIZE]
        for i in range(0, len(all_cs_files), BASELINE_BATCH_SIZE)
    ]

    for batch_idx, batch_paths in enumerate(batches):
        # Read each file from disk
        file_contents: list[tuple[str, str]] = []
        for rel_path in batch_paths:
            abs_path = os.path.join(repo_path, rel_path)
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    file_contents.append((rel_path, fh.read()))
            except OSError as exc:
                logger.warning("Cannot read %s: %s", abs_path, exc)

        if not file_contents:
            continue

        diagnostics = run_roslyn_analysis(file_contents)
        if diagnostics:
            # Baseline scans are stored against a sentinel event_id=0 or
            # we create a synthetic event.  For simplicity, use event_id=0
            # to mean "not tied to any webhook event".
            count = save_rule_results(
                db, event_id=0, diagnostics=diagnostics, scan_type="baseline",
            )
            total_issues += count

        logger.info(
            "Baseline batch %d/%d (%d files) done for %s",
            batch_idx + 1, len(batches), len(batch_paths), repository,
        )

    _update_scan_config(
        db, repository,
        baseline_scan_status="done",
        is_baseline_scanned=True,
        total_files=len(all_cs_files),
        total_issues=total_issues,
    )

    return {
        "status": "done",
        "total_files": len(all_cs_files),
        "total_issues": total_issues,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_repo_scan_config(repository: str, db: Session) -> RepoScanConfig:
    """Return the existing ``RepoScanConfig`` for *repository* or create one.

    New configs default to ``is_baseline_scanned=False`` so that the next
    webhook will trigger a baseline scan.
    """
    config = db.scalar(
        select(RepoScanConfig).where(RepoScanConfig.repository == repository)
    )
    if config is None:
        config = RepoScanConfig(
            repository=repository,
            analyze_paths=get_analyze_paths(repository, db),
            is_baseline_scanned=False,
        )
        db.add(config)
        db.flush()
    return config


def _update_scan_config(
    db: Session,
    repository: str,
    *,
    baseline_scan_status: str | None = None,
    is_baseline_scanned: bool | None = None,
    total_files: int | None = None,
    total_issues: int | None = None,
) -> None:
    config = db.scalar(
        select(RepoScanConfig).where(RepoScanConfig.repository == repository)
    )
    if config is None:
        return
    if baseline_scan_status is not None:
        config.baseline_scan_status = baseline_scan_status
    if is_baseline_scanned is not None:
        config.is_baseline_scanned = is_baseline_scanned
    if total_files is not None:
        config.baseline_total_files = total_files
    if total_issues is not None:
        config.baseline_total_issues = total_issues
    db.flush()


def is_baseline_needed(repository: str, db: Session) -> bool:
    """Return ``True`` when *repository* needs a first-time baseline scan."""
    config = db.scalar(
        select(RepoScanConfig).where(RepoScanConfig.repository == repository)
    )
    if config is None:
        return True  # never seen this repo — scan needed
    return not config.is_baseline_scanned
