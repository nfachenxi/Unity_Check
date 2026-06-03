"""Tests for rule_service.py — diff parsing, path filtering, result persisting."""

import pytest
from sqlalchemy import text

from unity_check.models import GithubEvent, RepoScanConfig, RuleResult
from unity_check.rule_service import (
    _normalize_paths,
    extract_cs_files_from_diff,
    filter_analyze_targets,
    get_analyze_paths,
    is_baseline_needed,
    save_rule_results,
)


# ---------------------------------------------------------------------------
# normalize_paths
# ---------------------------------------------------------------------------
class TestNormalizePaths:
    def test_strips_trailing_slashes(self):
        assert _normalize_paths(["Assets/Scripts/"]) == ["Assets/Scripts"]

    def test_strips_trailing_backslashes(self):
        assert _normalize_paths(["Assets\\Scripts\\"]) == ["Assets\\Scripts"]

    def test_filters_empty_strings(self):
        assert _normalize_paths(["", "  ", "Assets/Scripts"]) == ["Assets/Scripts"]

    def test_preserves_non_empty(self):
        paths = ["Assets/Scripts", "Assets/Editor", "Assets/Plugins"]
        assert _normalize_paths(paths) == paths


# ---------------------------------------------------------------------------
# extract_cs_files_from_diff
# ---------------------------------------------------------------------------
class TestExtractCsFilesFromDiff:
    def test_extracts_single_cs_file(self):
        diff = """diff --git a/Assets/Scripts/Player.cs b/Assets/Scripts/Player.cs
index abc..def
--- a/Assets/Scripts/Player.cs
+++ b/Assets/Scripts/Player.cs
@@ -1,3 +1,4 @@
+using UnityEngine;"""
        files = extract_cs_files_from_diff(diff)
        assert files == ["Assets/Scripts/Player.cs"]

    def test_extracts_multiple_cs_files(self):
        diff = """diff --git a/Assets/Scripts/A.cs b/Assets/Scripts/A.cs
diff --git a/Assets/Scripts/B.cs b/Assets/Scripts/B.cs
diff --git a/Assets/Textures/logo.png b/Assets/Textures/logo.png"""
        files = extract_cs_files_from_diff(diff)
        assert "Assets/Scripts/A.cs" in files
        assert "Assets/Scripts/B.cs" in files
        assert "logo.png" not in files

    def test_ignores_non_cs_files(self):
        diff = """diff --git a/README.md b/README.md
diff --git a/Assets/data.json b/Assets/data.json"""
        files = extract_cs_files_from_diff(diff)
        assert files == []

    def test_handles_empty_diff(self):
        assert extract_cs_files_from_diff("") == []
        assert extract_cs_files_from_diff(None) == []  # type: ignore

    def test_deduplicates_same_file(self):
        diff = """diff --git a/A.cs b/A.cs
diff --git a/A.cs b/A.cs"""
        files = extract_cs_files_from_diff(diff)
        assert files == ["A.cs"]

    def test_case_insensitive_extension(self):
        diff = "diff --git a/Assets/X.CS b/Assets/X.CS"
        files = extract_cs_files_from_diff(diff)
        assert files == ["Assets/X.CS"]


# ---------------------------------------------------------------------------
# filter_analyze_targets
# ---------------------------------------------------------------------------
class TestFilterAnalyzeTargets:
    def test_keeps_paths_under_prefix(self):
        targets = filter_analyze_targets(
            ["Assets/Scripts/Player.cs", "Assets/Plugins/Foo.cs"],
            ["Assets/Scripts"],
        )
        assert targets == ["Assets/Scripts/Player.cs"]

    def test_keeps_exact_prefix_match(self):
        targets = filter_analyze_targets(
            ["Assets/Scripts/Player.cs"],
            ["Assets/Scripts"],
        )
        assert targets == ["Assets/Scripts/Player.cs"]

    def test_backslash_normalization(self):
        targets = filter_analyze_targets(
            ["Assets\\Scripts\\Player.cs"],
            ["Assets/Scripts"],
        )
        assert targets == ["Assets\\Scripts\\Player.cs"]

    def test_empty_analyze_paths_returns_empty(self):
        targets = filter_analyze_targets(
            ["Assets/Scripts/Player.cs"],
            [],
        )
        assert targets == []

    def test_multiple_prefixes(self):
        targets = filter_analyze_targets(
            [
                "Assets/Scripts/Player.cs",
                "Assets/Editor/Tool.cs",
                "Assets/Plugins/Third.cs",
            ],
            ["Assets/Scripts", "Assets/Editor"],
        )
        assert len(targets) == 2
        assert "Assets/Scripts/Player.cs" in targets
        assert "Assets/Editor/Tool.cs" in targets

    def test_prefix_not_a_substring(self):
        targets = filter_analyze_targets(
            ["Assets/ScriptsExtra/Foo.cs"],
            ["Assets/Scripts"],
        )
        assert targets == []


# ---------------------------------------------------------------------------
# get_analyze_paths
# ---------------------------------------------------------------------------
class TestGetAnalyzePaths:
    def test_from_db_config(self, session):
        repo = "test/repo-custom"
        config = RepoScanConfig(
            repository=repo,
            analyze_paths=["Custom/Path"],
        )
        session.add(config)
        session.commit()

        paths = get_analyze_paths(repo, session)
        assert paths == ["Custom/Path"]

    def test_fallback_to_default(self, session, monkeypatch):
        from unity_check.config import get_settings

        monkeypatch.setattr(
            get_settings(), "default_analyze_paths", "Fallback/Path,Other/Path"
        )
        paths = get_analyze_paths("unknown/repo", session)
        assert "Fallback/Path" in paths
        assert "Other/Path" in paths

    def test_empty_db_config_falls_back(self, session, monkeypatch):
        """Repo exists but has empty analyze_paths → fallback."""
        from unity_check.config import get_settings

        monkeypatch.setattr(
            get_settings(), "default_analyze_paths", "Default/Path"
        )
        config = RepoScanConfig(
            repository="test/empty-paths",
            analyze_paths=[],
        )
        session.add(config)
        session.commit()

        paths = get_analyze_paths("test/empty-paths", session)
        assert paths == ["Default/Path"]


# ---------------------------------------------------------------------------
# is_baseline_needed
# ---------------------------------------------------------------------------
class TestIsBaselineNeeded:
    def test_true_when_no_config(self, session):
        assert is_baseline_needed("never/seen", session) is True

    def test_true_when_not_scanned(self, session):
        config = RepoScanConfig(
            repository="test/not-scanned",
            is_baseline_scanned=False,
        )
        session.add(config)
        session.commit()
        assert is_baseline_needed("test/not-scanned", session) is True

    def test_false_when_scanned(self, session):
        config = RepoScanConfig(
            repository="test/scanned",
            is_baseline_scanned=True,
        )
        session.add(config)
        session.commit()
        assert is_baseline_needed("test/scanned", session) is False


# ---------------------------------------------------------------------------
# save_rule_results
# ---------------------------------------------------------------------------
class TestSaveRuleResults:
    def test_saves_and_returns_count(self, session):
        event = GithubEvent(
            delivery_id="rules-save-test",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        diagnostics = [
            {
                "id": "CA1822",
                "title": "Member can be marked as static",
                "filePath": "Assets/Scripts/Test.cs",
                "startLine": 10,
                "startColumn": 5,
                "severity": "Warning",
                "category": "Performance",
                "message": "Member 'Update' can be static",
                "snippet": "void Update()",
            },
            {
                "id": "SA1300",
                "title": "Element should begin with upper-case",
                "filePath": "Assets/Scripts/Test.cs",
                "startLine": 2,
                "startColumn": 1,
                "severity": "Warning",
                "category": "Naming",
                "message": "Element 'myField' should begin with an upper-case letter",
                "snippet": "private int myField;",
            },
        ]

        count = save_rule_results(session, event_id, diagnostics, "incremental")
        assert count == 2

        # Verify rows in DB
        rows = session.query(RuleResult).filter_by(event_id=event_id).all()
        assert len(rows) == 2
        assert rows[0].rule_id == "CA1822"
        assert rows[1].rule_id == "SA1300"

    def test_idempotent_on_second_save(self, session):
        event = GithubEvent(
            delivery_id="rules-idempotent-test",
            event_type="push",
            payload={},
            status="success",
        )
        session.add(event)
        session.commit()
        event_id = event.id

        diagnostics = [
            {
                "id": "RCS1005",
                "title": "Simplify null check",
                "filePath": "A.cs",
                "startLine": 1,
                "startColumn": 1,
                "severity": "Info",
                "category": "Simplification",
                "message": "Use 'is null'",
                "snippet": "x == null",
            },
        ]

        c1 = save_rule_results(session, event_id, diagnostics, "incremental")
        assert c1 == 1
        c2 = save_rule_results(session, event_id, diagnostics, "incremental")
        assert c2 == 1  # old row replaced

        rows = session.query(RuleResult).filter_by(event_id=event_id).all()
        assert len(rows) == 1

    def test_empty_diagnostics(self, session):
        event = GithubEvent(
            delivery_id="rules-empty-test",
            event_type="push",
            payload={},
        )
        session.add(event)
        session.commit()

        count = save_rule_results(session, event.id, [], "incremental")
        assert count == 0
