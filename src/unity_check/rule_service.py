"""Diff-parsing utilities for extracting .cs files from unified git diffs."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

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
        if path.lower().endswith(".cs"):
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
