"""Git operations: bare repo clone/fetch, diff extraction, SHA parsing."""

import logging
import os
import re
import subprocess
import time
from typing import Any

from unity_check.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

# How many clone/fetch attempts across all mirror candidates (including the
# original URL as the final fallback).  Each attempt may try a different mirror.
MAX_CLONE_ATTEMPTS = 3
# Per-attempt timeout for ``git clone`` / ``git fetch``
CLONE_TIMEOUT_SECONDS = 120
# Per-mirror timeout for ``git ls-remote`` speed probe
MIRROR_PROBE_TIMEOUT = 10


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


def resolve_bare_path(clone_url: str) -> str:
    """Resolve the expected bare repo directory path for a clone URL.

    This is a pure path computation — it does not touch the filesystem or
    perform any git operations. Use it to check whether a bare repo already
    exists on disk without triggering a clone or fetch.
    """
    from unity_check.config import get_settings as _get_settings

    clone_base = os.path.abspath(_get_settings().git_clone_base_dir)
    repo_dir = _repo_name_from_url(clone_url)
    return os.path.join(clone_base, f"{repo_dir}.git")


def _probe_mirror_speed(mirror_base: str, clone_url: str) -> float | None:
    """Measure a mirror's response time via ``git ls-remote``.

    Returns the elapsed time in seconds on success, or ``None`` if the
    mirror is unreachable or times out (default: 10 s).
    """
    mirrored_url = mirror_base.rstrip("/") + "/" + clone_url
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["git", "ls-remote", mirrored_url],
            capture_output=True,
            timeout=MIRROR_PROBE_TIMEOUT,
            text=True,
        )
        if result.returncode == 0:
            elapsed = time.monotonic() - start
            logger.debug("Mirror %s responded in %.2fs", mirror_base, elapsed)
            return elapsed
        logger.debug("Mirror %s ls-remote returned code %d", mirror_base, result.returncode)
    except subprocess.TimeoutExpired:
        logger.debug("Mirror %s timed out after %ds", mirror_base, MIRROR_PROBE_TIMEOUT)
    except Exception as exc:
        logger.debug("Mirror %s probe failed: %s", mirror_base, exc)
    return None


def _rank_mirror_candidates(clone_url: str) -> list[str]:
    """Probe all configured mirrors and return candidate URLs, fastest first.

    The returned list is capped at *MAX_CLONE_ATTEMPTS* entries.  The
    original *clone_url* is always included as the final fallback.

    When no mirrors are configured, returns ``[clone_url]`` immediately
    (no probing).
    """
    cfg = get_settings()
    mirror_bases = cfg.get_mirror_urls()
    if not mirror_bases:
        return [clone_url]

    # Probe every mirror in parallel-ish (sequential is fine for a small set)
    responsive: list[tuple[float, str]] = []
    unresponsive: list[str] = []
    for mirror_base in mirror_bases:
        mirrored_url = mirror_base.rstrip("/") + "/" + clone_url
        latency = _probe_mirror_speed(mirror_base, clone_url)
        if latency is not None:
            responsive.append((latency, mirrored_url))
        else:
            unresponsive.append(mirrored_url)

    # Sort responsive mirrors by latency (fastest first)
    responsive.sort(key=lambda x: x[0])
    candidates = [url for _, url in responsive] + unresponsive

    # Cap: keep top (MAX_CLONE_ATTEMPTS - 1) mirrors, always include original
    result = candidates[: MAX_CLONE_ATTEMPTS - 1]
    if clone_url not in result:
        result.append(clone_url)

    logger.info(
        "Mirror probe: %d responsive, %d unresponsive → %d candidate(s)",
        len(responsive),
        len(unresponsive),
        len(result),
    )
    return result[:MAX_CLONE_ATTEMPTS]


def _clone_with_fallback(bare_path: str, original_url: str, candidates: list[str]) -> None:
    """Try ``git clone --bare`` with each candidate URL in order.

    On failure the partial clone directory is cleaned up before the next
    attempt.  Raises :class:`GitServiceError` when all candidates fail.
    """
    import git

    last_error: Exception | None = None
    for attempt, candidate_url in enumerate(candidates, 1):
        logger.info(
            "Clone attempt %d/%d: %s",
            attempt,
            len(candidates),
            candidate_url,
        )
        try:
            git.Repo.clone_from(candidate_url, bare_path, bare=True)
            logger.info("Clone succeeded on attempt %d", attempt)
            return
        except Exception as exc:
            last_error = exc
            logger.warning("Clone attempt %d/%d failed: %s", attempt, len(candidates), exc)
            # Remove partial clone directory left by a failed attempt
            if os.path.isdir(bare_path):
                import shutil as _su

                _su.rmtree(bare_path, ignore_errors=True)

    raise GitServiceError(
        f"Failed to clone bare repo from {original_url} after "
        f"{len(candidates)} attempt(s): {last_error}"
    ) from last_error


def _fetch_with_fallback(bare_path: str, candidates: list[str]) -> None:
    """Try ``git fetch`` on an existing bare repo with each candidate URL.

    Each attempt updates the ``origin`` remote URL before fetching.  Raises
    :class:`GitServiceError` when all candidates fail.
    """
    import git

    repo = git.Repo(bare_path)
    last_error: Exception | None = None

    for attempt, candidate_url in enumerate(candidates, 1):
        try:
            # Update remote URL to the current candidate
            current_url = _safe_cmd(lambda: repo.git.remote("get-url", "origin"))
            if current_url != candidate_url:
                repo.git.remote("set-url", "origin", candidate_url)

            # Ensure fetch refspec is present (fresh bare clones may lack it)
            has_refspec = _safe_cmd(
                lambda: bool(repo.git.config("--get", "remote.origin.fetch"))
            )
            if not has_refspec:
                repo.git.config("remote.origin.fetch", "+refs/heads/*:refs/heads/*")

            origin = repo.remote("origin")
            origin.fetch()
            logger.info("Fetch succeeded on attempt %d (%s)", attempt, candidate_url)
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Fetch attempt %d/%d failed: %s",
                attempt,
                len(candidates),
                exc,
            )

    raise GitServiceError(
        f"Failed to fetch bare repo at {bare_path} after "
        f"{len(candidates)} attempt(s): {last_error}"
    ) from last_error


def _safe_cmd(fn):
    """Run *fn*, returning its result or ``None`` on any exception."""
    try:
        return fn()
    except Exception:
        return None


def ensure_bare_repo(clone_url: str, ssh_key_path: str | None = None) -> str:
    """Clone a bare repo or fetch if it already exists.

    When multiple GitHub mirrors are configured (via ``GITHUB_MIRROR_URLS``
    in the environment / ``.env``), each is probed with ``git ls-remote``
    and the fastest responsive mirror is preferred.  Up to
    *MAX_CLONE_ATTEMPTS* mirrors (plus the original URL as the final
    fallback) are tried in order before giving up.

    When *ssh_key_path* is provided, the ``GIT_SSH_COMMAND`` environment
    variable is set for the duration of the operation, allowing private-repo
    access via the specified SSH key.

    Returns the absolute path to the bare repo directory.
    """
    import git

    clone_base = os.path.abspath(settings.git_clone_base_dir)
    repo_dir = _repo_name_from_url(clone_url)
    bare_path = os.path.join(clone_base, f"{repo_dir}.git")

    # Build candidate URLs: fastest mirrors first, original as fallback
    candidates = _rank_mirror_candidates(clone_url)

    # Set up SSH command if key path provided (restore in finally block)
    _prev_ssh = os.environ.get("GIT_SSH_COMMAND")
    if ssh_key_path:
        os.environ["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        )

    try:
        if os.path.isdir(bare_path):
            logger.info("Fetching existing bare repo: %s", bare_path)
            _fetch_with_fallback(bare_path, candidates)
        else:
            _clone_with_fallback(bare_path, clone_url, candidates)
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
                return repo.git.diff_tree("-r", "-p", "--root", after_sha)
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


def _object_exists(repo, sha: str) -> bool:
    """Check whether a git object (commit, tree, blob) exists in *repo*."""
    import git
    try:
        repo.git.cat_file("-e", sha)
        return True
    except Exception:
        return False


def generate_full_cs_diff(bare_repo_path: str, sha: str) -> str:
    """Generate a unified diff treating all tracked .cs files as new additions.

    For each tracked ``.cs`` file in the commit, extracts the full file
    content and formats it as a unified diff against ``/dev/null``.  Unlike
    the well-known empty-tree-SHA approach (``4b825dc642cb6eb9a060e54bf899d15303643e6c``),
    this does **not** depend on a special Git object existing in the bare
    repository — it reads file content directly via the GitPython blob API,
    making it reliable in any repository state (fresh clone, bare repo,
    shallow clone, etc.).

    The output is a standard unified diff (``--- /dev/null`` /
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

    # For each .cs file, get the full content and format as a unified diff
    # against /dev/null (all files appear as new additions).
    #
    # This approach is more reliable than diff-tree against the empty tree
    # hash because the empty tree object (4b825dc...) is NOT guaranteed to
    # exist in bare repositories — and even when explicitly created, it can
    # still be invisible to subsequent GitPython diff-tree calls.
    diff_blocks: list[str] = []
    for fp in cs_files:
        try:
            blob = commit.tree / fp
            raw = blob.data_stream.read()
            content = raw.decode("utf-8", errors="replace")
            lines = content.split("\n")
            # Remove trailing empty line produced by split for trailing newlines
            if lines and lines[-1] == "":
                lines = lines[:-1]

            diff_block = (
                f"diff --git a/{fp} b/{fp}\n"
                f"new file mode 100644\n"
                f"index 0000000..{blob.hexsha}\n"
                f"--- /dev/null\n"
                f"+++ b/{fp}\n"
                f"@@ -0,0 +1,{len(lines)} @@\n"
            )
            for line in lines:
                diff_block += f"+{line}\n"

            diff_blocks.append(diff_block)
        except Exception as exc:
            logger.warning(
                "Failed to extract content for %s from commit %s: %s",
                fp, sha, exc,
            )
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
