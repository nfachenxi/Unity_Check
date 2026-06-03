"""Git operations: bare repo clone/fetch, diff extraction, SHA parsing."""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from unity_check.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# Per-path locks to prevent concurrent fetch/clone on the same bare repo.
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    with _locks_lock:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


class GitServiceError(Exception):
    """Raised when a git operation cannot be completed."""


def _repo_name_from_url(clone_url: str) -> str:
    """Extract a safe directory name from a git clone URL.

    Example: 'git@github.com:owner/repo.git' -> 'owner_repo'
    """
    # Strip protocol prefix, git@ prefix, and .git suffix
    cleaned = clone_url.strip()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^git@", "", cleaned)
    cleaned = re.sub(r"\.git/*$", "", cleaned)
    # Replace separators with underscore
    cleaned = re.sub(r"[/:@.]", "_", cleaned)
    # Collapse repeated underscores
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_") or "unknown"


def _ssh_command() -> str | None:
    """Build GIT_SSH_COMMAND value when a key path is configured."""
    key_path = settings.git_ssh_key_path.strip()
    if not key_path:
        return None
    if not os.path.exists(key_path):
        raise GitServiceError(f"SSH key not found: {key_path}")
    return f"ssh -i {key_path} -o StrictHostKeyChecking=accept-new"


def ensure_bare_repo(clone_url: str) -> str:
    """Clone a bare repo or fetch if it already exists.

    Returns the absolute path to the bare repo directory.
    """
    import git

    clone_base = os.path.abspath(
        os.environ.get("GIT_CLONE_BASE_DIR", "./repos")
    )
    repo_dir = _repo_name_from_url(clone_url)
    bare_path = os.path.join(clone_base, f"{repo_dir}.git")

    lock = _get_lock(bare_path)
    with lock:
        ssh_cmd = _ssh_command()
        env = os.environ.copy()
        if ssh_cmd:
            env["GIT_SSH_COMMAND"] = ssh_cmd

        if os.path.isdir(bare_path):
            logger.info("Fetching existing bare repo: %s", bare_path)
            try:
                repo = git.Repo(bare_path)
                origin = repo.remote("origin")
                # Ensure fetch refspec is set (local path clones may lack it)
                if not origin.fetch_refspec:
                    repo.git.config(
                        f"--add remote.origin.fetch +refs/heads/*:refs/heads/*"
                    )
                origin.fetch(env=env)
            except Exception as exc:
                raise GitServiceError(
                    f"Failed to fetch bare repo at {bare_path}: {exc}"
                ) from exc
        else:
            logger.info("Cloning bare repo: %s -> %s", clone_url, bare_path)
            try:
                git.Repo.clone_from(
                    clone_url,
                    bare_path,
                    bare=True,
                    env=env,
                )
            except Exception as exc:
                raise GitServiceError(
                    f"Failed to clone bare repo from {clone_url}: {exc}"
                ) from exc

    return bare_path


def get_diff(bare_repo_path: str, before_sha: str, after_sha: str) -> str:
    """Extract diff between two SHAs from a bare repo.

    When before_sha is the null SHA (all zeros, first push), returns the
    diff of the after_sha commit against its parent (or the full tree).
    """
    import git

    if not os.path.isdir(bare_repo_path):
        raise GitServiceError(f"Bare repo not found: {bare_repo_path}")

    try:
        repo = git.Repo(bare_repo_path)
    except Exception as exc:
        raise GitServiceError(f"Failed to open repo {bare_repo_path}: {exc}") from exc

    # Guard: make sure the SHAs exist in the repo
    def _sha_exists(sha: str) -> bool:
        try:
            repo.commit(sha)
            return True
        except Exception:
            return False

    # Null before-sha: first push — diff the single commit
    null_sha_pattern = re.fullmatch(r"0{40}", before_sha or "")
    if null_sha_pattern or not before_sha:
        logger.info("Null before_sha detected, diffing single commit %s", after_sha)
        try:
            if _sha_exists(after_sha):
                return repo.git.diff_tree("-r", "-p", after_sha)
            else:
                logger.warning("after_sha not found in repo: %s", after_sha)
                return ""
        except Exception as exc:
            logger.warning("diff-tree failed for %s: %s", after_sha, exc)
            return ""

    # Normal two-SHA diff
    if not _sha_exists(before_sha) or not _sha_exists(after_sha):
        missing = []
        if not _sha_exists(before_sha):
            missing.append(f"before={before_sha}")
        if not _sha_exists(after_sha):
            missing.append(f"after={after_sha}")
        logger.warning("SHA(s) not found in bare repo: %s", ", ".join(missing))
        return ""

    try:
        return repo.git.diff(f"{before_sha}..{after_sha}")
    except Exception as exc:
        logger.warning("diff failed for %s..%s: %s", before_sha, after_sha, exc)
        return ""


def extract_sha_from_payload(
    payload: dict[str, Any], event_type: str
) -> tuple[str | None, str | None]:
    """Extract before/after SHA from a GitHub webhook payload.

    Returns (before_sha, after_sha). Either may be None if not found.
    """
    if not isinstance(payload, dict):
        return None, None

    if event_type == "push":
        before = payload.get("before")
        after = payload.get("after")
        return (str(before) if before else None, str(after) if after else None)

    if event_type == "pull_request":
        pr = payload.get("pull_request") or {}
        base = pr.get("base") or {}
        head = pr.get("head") or {}
        before = base.get("sha")
        after = head.get("sha")
        return (str(before) if before else None, str(after) if after else None)

    return None, None


def extract_clone_url_from_payload(payload: dict[str, Any]) -> str | None:
    """Extract the SSH clone URL from a GitHub webhook payload.

    Prefers ssh_url; falls back to clone_url (https).
    """
    if not isinstance(payload, dict):
        return None

    # push: repository is at top level
    if "repository" in payload:
        repo = payload["repository"] or {}
        return repo.get("ssh_url") or repo.get("clone_url")

    # pull_request: head repo may differ from base
    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    if head_repo:
        return head_repo.get("ssh_url") or head_repo.get("clone_url")

    return None
