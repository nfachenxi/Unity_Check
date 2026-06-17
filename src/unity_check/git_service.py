"""Git operations: bare repo clone/fetch, diff extraction, SHA parsing."""

import logging
import os
import re
from typing import Any

from unity_check.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class GitServiceError(Exception):
    """Raised when a git operation cannot be completed."""


def _repo_name_from_url(clone_url: str) -> str:
    """Extract a safe directory name from a git clone URL.

    Example: 'git@github.com:owner/repo.git' -> 'owner_repo'
    """
    cleaned = clone_url.strip()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^git@", "", cleaned)
    cleaned = re.sub(r"\.git/*$", "", cleaned)
    cleaned = re.sub(r"[/:@.]", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_") or "unknown"


def ensure_bare_repo(clone_url: str, ssh_key_path: str | None = None) -> str:
    """Clone a bare repo or fetch if it already exists.

    When *ssh_key_path* is provided, the ``GIT_SSH_COMMAND``
    environment variable is set for the duration of the operation,
    allowing private-repo access via the specified SSH key.

    Returns the absolute path to the bare repo directory.
    """
    import git

    clone_base = os.path.abspath(settings.git_clone_base_dir)
    repo_dir = _repo_name_from_url(clone_url)
    bare_path = os.path.join(clone_base, f"{repo_dir}.git")

    # Set up SSH command if key path provided (restore in finally block)
    _prev_ssh = os.environ.get("GIT_SSH_COMMAND")
    if ssh_key_path:
        os.environ["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        )

    try:
        if os.path.isdir(bare_path):
            logger.info("Fetching existing bare repo: %s", bare_path)
            try:
                repo = git.Repo(bare_path)
                try:
                    has_refspec = bool(repo.git.config("--get", "remote.origin.fetch"))
                except Exception:
                    has_refspec = False
                if not has_refspec:
                    repo.git.remote("set-url", "origin", clone_url)
                    repo.git.config("remote.origin.fetch", "+refs/heads/*:refs/heads/*")
                origin = repo.remote("origin")
                origin.fetch()
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
                )
            except Exception as exc:
                raise GitServiceError(
                    f"Failed to clone bare repo from {clone_url}: {exc}"
                ) from exc
    finally:
        if ssh_key_path:
            if _prev_ssh:
                os.environ["GIT_SSH_COMMAND"] = _prev_ssh
            else:
                os.environ.pop("GIT_SSH_COMMAND", None)

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


def generate_full_cs_diff(bare_repo_path: str, sha: str) -> str:
    """Generate a unified diff treating all tracked .cs files as new additions.

    Uses ``git diff-tree -p --root <sha> -- <path>`` for each tracked ``.cs``
    file so the output is a standard unified diff (``--- /dev/null`` /
    ``+++ b/<path>``) that the existing evaluation pipeline
    (``extract_cs_files_from_diff`` / ``_extract_file_diff``) can process
    without modification.

    Returns an empty string when no ``.cs`` files are found.
    """
    import git

    if not os.path.isdir(bare_repo_path):
        raise GitServiceError(f"Bare repo not found: {bare_repo_path}")

    try:
        repo = git.Repo(bare_repo_path)
    except Exception as exc:
        raise GitServiceError(f"Failed to open repo {bare_repo_path}: {exc}") from exc

    # Verify the commit exists
    try:
        commit = repo.commit(sha)
    except Exception as exc:
        raise GitServiceError(f"Commit {sha} not found in bare repo: {exc}") from exc

    # List all tracked .cs files in this commit's tree
    cs_files = sorted(
        b.path for b in commit.tree.traverse()
        if b.type == "blob" and b.path.lower().endswith(".cs")
    )
    if not cs_files:
        logger.info("No .cs files found in commit %s", sha)
        return ""

    # Generate a per-file diff from /dev/null
    diff_blocks: list[str] = []
    for fp in cs_files:
        try:
            block = repo.git.diff_tree("-p", "--root", sha, "--", fp)
            if block:
                diff_blocks.append(block)
        except Exception as exc:
            logger.warning("Failed to diff %s from commit %s: %s", fp, sha, exc)
            continue

    if not diff_blocks:
        return ""

    return "\n".join(diff_blocks)


def get_default_branch_head(bare_repo_path: str) -> str | None:
    """Resolve the default branch HEAD SHA from a bare repo.

    Resolution order:
    1. ``HEAD`` symbolic reference
    2. Common branch names (``main`` / ``master`` / ``develop``)
    3. First available branch as a last resort

    Returns ``None`` when no commit can be resolved.
    """
    import git

    if not os.path.isdir(bare_repo_path):
        raise GitServiceError(f"Bare repo not found: {bare_repo_path}")

    try:
        repo = git.Repo(bare_repo_path)
    except Exception as exc:
        raise GitServiceError(f"Failed to open repo {bare_repo_path}: {exc}") from exc

    # 1. Resolve HEAD directly
    try:
        return repo.head.commit.hexsha
    except Exception:
        pass

    # 2. Common branch names
    for branch in ("main", "master", "develop"):
        try:
            return repo.commit(branch).hexsha
        except Exception:
            continue

    # 3. Any available branch
    try:
        for b in repo.branches:
            return b.commit.hexsha
    except Exception:
        pass

    return None


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
    """Extract the clone URL from a GitHub webhook payload.

    Prefers clone_url (https); falls back to ssh_url.
    """
    if not isinstance(payload, dict):
        return None

    if "repository" in payload:
        repo = payload["repository"] or {}
        return repo.get("clone_url") or repo.get("ssh_url")

    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    if head_repo:
        return head_repo.get("clone_url") or head_repo.get("ssh_url")

    return None
