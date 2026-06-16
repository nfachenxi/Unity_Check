"""Webhook verification, signature validation, and ref matching."""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import logging

from sqlalchemy.orm import Session

from unity_check.models import Repository
from unity_check.repository_service import get_repository_by_name

logger = logging.getLogger(__name__)


def verify_webhook_signature(
    payload_bytes: bytes,
    secret: str,
    signature_header: str,
) -> bool:
    """Verify X-Hub-Signature-256 against a webhook secret.

    Returns True when the signature matches, False otherwise.
    """
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header.strip())


def match_branch(ref: str, branch_filter: str | None) -> bool:
    """Check if a git ref matches branch filter patterns (fnmatch).

    Returns True when no filter is configured (all branches allowed).
    Patterns are stored as a JSON array string, e.g. ``["main", "release/*"]``.
    """
    if not branch_filter:
        return True

    branch = ref.replace("refs/heads/", "", 1) if ref.startswith("refs/heads/") else ref

    try:
        patterns = json.loads(branch_filter)
    except (json.JSONDecodeError, TypeError):
        return True

    if not isinstance(patterns, list) or not patterns:
        return True

    return any(fnmatch.fnmatch(branch, p) for p in patterns)


def lookup_repository(db: Session, repo_name: str) -> Repository | None:
    """Look up a registered repository by full name (e.g. ``owner/repo``)."""
    return get_repository_by_name(db, repo_name)
