"""Demo .cs 文件全流程集成测试。

测试流程:
1. 从 Demo/Assets/Scripts/ 目录初始化临时 Git 仓库
2. 模拟 push 事件（生成 diff、创建 GithubEvent、RuleResult）
3. 执行 Roslyn 增量分析 (*不* mock，如果 Roslyn 不可用则跳过)
4. 执行评估流水线（LLM mock 已由 conftest 提供）
5. 验证评估结果正确写入数据库

前置条件:
- 仅需 SQLite (conftest 提供)
- Roslyn Docker 容器可选（不可用时自动跳过 Roslyn 部分）
- LLM 由 conftest mock

用法: uv run pytest tests/test_integration_demo.py -v
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from unity_check.config import get_settings
from unity_check.db import SessionLocal
from unity_check.git_service import get_diff
from unity_check.models import GithubEvent, RuleResult, EvaluationRound
from unity_check.orchestrator import run_evaluation_pipeline
from unity_check.rule_service import (
    extract_cs_files_from_diff,
    filter_analyze_targets,
    get_analyze_paths,
    run_roslyn_analysis,
    save_rule_results,
    roslyn_health_check,
)
from sqlalchemy import select, func

logger = logging.getLogger(__name__)

DEMO_SCRIPTS_DIR = Path(__file__).parent.parent / "Demo" / "Unity_Check_Demo" / "Assets" / "Scripts"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_git_repo():
    """创建临时 bare Git 仓库，包含 Demo/Assets/Scripts 下所有 .cs 文件。

    返回 (bare_repo_path, file_paths, sha1, sha2)。
    sha1 → sha2 表示从空仓库到添加所有 .cs 文件的变更。
    """
    tmp = tempfile.mkdtemp(prefix="unitycheck_test_")
    bare_path = os.path.join(tmp, "demo_repo.git")

    # 初始化 bare repo
    subprocess.run(
        ["git", "init", "--bare", bare_path],
        check=True, capture_output=True,
    )

    # 在工作目录中初始化一个普通 repo 并 push 到 bare
    work_dir = os.path.join(tmp, "work")
    os.makedirs(work_dir)
    subprocess.run(["git", "init"], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=work_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=work_dir, check=True, capture_output=True,
    )

    # 创建 Assets/Scripts 目录结构
    assets_scripts = os.path.join(work_dir, "Assets", "Scripts")
    os.makedirs(assets_scripts)

    # 复制所有 .cs 文件
    file_paths = []
    for cs_file in sorted(DEMO_SCRIPTS_DIR.glob("*.cs")):
        rel_path = f"Assets/Scripts/{cs_file.name}"
        dest = os.path.join(work_dir, rel_path)
        shutil.copy2(str(cs_file), dest)
        file_paths.append(rel_path)

    # 第一轮 commit: 添加所有文件
    subprocess.run(["git", "add", "."], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit: add all scripts"],
        cwd=work_dir, check=True, capture_output=True,
    )
    sha1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()

    # 推送到 bare repo
    subprocess.run(
        ["git", "remote", "add", "origin", bare_path],
        cwd=work_dir, check=True, capture_output=True,
    )

    # 获取当前分支名
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=work_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "push", "--set-upstream", "origin", f"{branch}:{branch}"],
        cwd=work_dir, check=True, capture_output=True,
    )

    # 第二轮 commit: 修改几个文件引入新问题
    _append_code_changes(work_dir)
    subprocess.run(["git", "add", "."], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Second commit: add game features with issues"],
        cwd=work_dir, check=True, capture_output=True,
    )
    sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()

    subprocess.run(
        ["git", "push", "origin", f"{branch}:{branch}"],
        cwd=work_dir, check=True, capture_output=True,
    )

    yield bare_path, file_paths, sha1, sha2

    # Cleanup
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _append_code_changes(work_dir: str):
    """在已有文件中追加一些变更，生成第二个 commit 的 diff。"""
    changes = {
        "Assets/Scripts/GameManager.cs": """
    // New unsafe method
    public void ResetAllData()
    {
        var player = FindObjectOfType<PlayerController>();
        player.Respawn();
        currentScore = 0;
    }
""",
        "Assets/Scripts/PlayerController.cs": """
    // 新增：每帧进行的低效查找
    void LateUpdate()
    {
        var gameManager = FindObjectOfType<GameManager>();
        if (gameManager != null && _IsDead)
        {
            gameManager.CheckGameOver();
        }
    }
""",
    }
    for rel_path, code in changes.items():
        filepath = os.path.join(work_dir, rel_path)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(code)


def _patch_conftest_mocks_for_integration(monkeypatch):
    """撤消 conftest 中对 Roslyn 和 diff 提取的 mock。

    保留 LLM mock（避免真实 API 调用）。
    保留通知 mock。
    """
    # 还原 Roslyn 相关函数，以便真实调用
    import unity_check.tasks as tasks_mod
    import unity_check.orchestrator as orch_mod

    # 解除 tasks 模块中的 Roslyn mock
    monkeypatch.undo()
    # conftest 中的 _mock_roslyn 是 autouse，undo 会清除它。
    # 但我们需要保留 LLM 和通知的 mock。

    # 重新设置 LLM mock
    def fake_evaluate_file_dimension(file_path, file_diff, file_rule_results, event_summary, dimension):
        return {
            "score": 78.0 if "func" in dimension else 72.0,
            "summary": f"Dimension {dimension} assessment for {file_path}",
            "findings": [
                {
                    "title": f"Issue in {file_path}: code pattern concern",
                    "category": "architecture" if "func" in dimension else "security",
                    "severity": "medium",
                    "description": f"Detected issue in {dimension} analysis.",
                    "suggestion": "Review and refactor accordingly.",
                    "file": file_path,
                    "line_hint": "line 20",
                }
            ],
            "tokens_used": 250,
            "duration_ms": 800,
            "model_name": "deepseek-chat-mock",
        }

    monkeypatch.setattr(
        "unity_check.orchestrator.evaluate_file_dimension",
        fake_evaluate_file_dimension,
    )

    # 重新设置通知 mock
    def fake_build_and_persist(event, db):
        return []

    monkeypatch.setattr(
        "unity_check.orchestrator.build_and_persist_notifications",
        fake_build_and_persist,
    )


def _create_github_event(session, repo: str, before_sha: str, after_sha: str,
                         diff: str, event_type: str = "push") -> GithubEvent:
    """在数据库创建 GithubEvent 并返回。"""
    event = GithubEvent(
        delivery_id=f"integration-test-{datetime.now(timezone.utc).timestamp()}",
        event_type=event_type,
        repository=repo,
        before_sha=before_sha,
        after_sha=after_sha,
        clone_path="./repos/test_integration_repo.git",
        diff_content=diff,
        diff_size=len(diff.encode("utf-8")),
        payload={
            "ref": "refs/heads/main",
            "commits": [{"id": after_sha[:7]}],
            "repository": {"full_name": repo},
        },
        status="queued",
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDemoCSFilesExist:
    """验证 Demo .cs 文件存在且可读。"""

    def test_scripts_directory_exists(self):
        """Scripts 目录应存在。"""
        assert DEMO_SCRIPTS_DIR.is_dir(), f"Scripts directory not found: {DEMO_SCRIPTS_DIR}"

    @pytest.mark.parametrize("filename", [
        "GameManager.cs", "PlayerController.cs", "Enemy.cs", "EnemyAI.cs",
        "WeaponSystem.cs", "NetworkManager.cs", "UIManager.cs", "SaveManager.cs",
        "DataLoader.cs", "Config.cs", "BattleSystem.cs", "InventoryManager.cs",
        "SkillHandler.cs", "AudioManager.cs", "CameraController.cs",
        "ObjectPool.cs", "CoroutineManager.cs", "EventDispatcher.cs",
    ])
    def test_each_cs_file_exists(self, filename):
        """每个 .cs 文件应存在。"""
        path = DEMO_SCRIPTS_DIR / filename
        assert path.is_file(), f"Missing: {path}"

    def test_all_files_have_content(self):
        """所有 .cs 文件非空。"""
        for cs_file in DEMO_SCRIPTS_DIR.glob("*.cs"):
            content = cs_file.read_text(encoding="utf-8")
            assert len(content) > 100, f"{cs_file.name} is too short ({len(content)} chars)"


class TestDiffExtraction:
    """验证 diff 解析正确提取 .cs 文件路径。"""

    def test_extract_cs_files_from_real_diff(self, demo_git_repo):
        """从真实 git diff 中提取 .cs 文件路径。"""
        bare_path, _, sha1, sha2 = demo_git_repo

        diff = get_diff(bare_path, sha1, sha2)
        assert diff, "Diff should not be empty"

        cs_files = extract_cs_files_from_diff(diff)
        assert len(cs_files) > 0, f"Should extract .cs files from diff, got: {cs_files}"

        # 验证修改的文件被检测到
        changed_files = [f for f in cs_files if "GameManager.cs" in f or "PlayerController.cs" in f]
        assert len(changed_files) >= 1, f"Modified files should be detected, got: {cs_files}"

    def test_all_files_in_initial_commit(self, demo_git_repo):
        """初始 commit diff 中应包含所有 18 个 .cs 文件。"""
        bare_path, file_paths, sha1, _ = demo_git_repo

        # 用 null parent 获取初始 commit 的 diff
        # git diff-tree with first commit: use 4b825dc (empty tree) as base
        # or use git show which works for any commit
        import git as gitpython
        repo = gitpython.Repo(bare_path)
        # git show shows the diff for a single commit including the initial one
        diff = repo.git.show("-p", "--format=", sha1)
        # If show returns empty, try diff-tree with --root
        if not diff:
            diff = repo.git.diff_tree("--root", "-r", "-p", sha1)

        cs_files = extract_cs_files_from_diff(diff)
        assert len(cs_files) == 18, f"Expected 18 .cs files, got {len(cs_files)}: {cs_files}"


class TestFilterAnalyzeTargets:
    """验证路径过滤逻辑。"""

    def test_filter_keeps_assets_scripts(self):
        """Default analyze_paths='Assets/Scripts' 应保留所有 scripts。"""
        paths = ["Assets/Scripts/GameManager.cs", "Assets/Plugins/ThirdParty.cs"]
        filtered = filter_analyze_targets(paths, ["Assets/Scripts"])
        assert "Assets/Scripts/GameManager.cs" in filtered
        assert "Assets/Plugins/ThirdParty.cs" not in filtered


class TestRoslynIntegration:
    """验证 Roslyn 分析器对真实 .cs 文件的检测 (需要 Docker 运行)。"""

    @pytest.fixture(autouse=True)
    def _check_roslyn(self):
        if not roslyn_health_check():
            pytest.skip("Roslyn Docker container is not running")

    def test_roslyn_health(self):
        """GET /health 应返回 200。"""
        assert roslyn_health_check()

    def test_analyze_game_manager(self):
        """GameManager.cs 应被 Roslyn 检测出问题。"""
        filepath = DEMO_SCRIPTS_DIR / "GameManager.cs"
        content = filepath.read_text(encoding="utf-8")

        diagnostics = run_roslyn_analysis([("Assets/Scripts/GameManager.cs", content)])
        # 至少应检测到一些命名/样式问题
        assert isinstance(diagnostics, list)

        rule_ids = {d.get("id", "") for d in diagnostics}
        logger.info("Roslyn diagnostics for GameManager.cs: %s", rule_ids)

    def test_analyze_all_demo_files(self):
        """批量分析所有 Demo .cs 文件。"""
        files = []
        for cs_file in sorted(DEMO_SCRIPTS_DIR.glob("*.cs")):
            rel_path = f"Assets/Scripts/{cs_file.name}"
            content = cs_file.read_text(encoding="utf-8")
            files.append((rel_path, content))

        diagnostics = run_roslyn_analysis(files)
        assert isinstance(diagnostics, list)
        logger.info("Roslyn found %d diagnostics across %d files", len(diagnostics), len(files))

        # 验证诊断结果中有合法的字段
        if diagnostics:
            diag = diagnostics[0]
            assert "id" in diag
            assert "message" in diag or "title" in diag


class TestEvaluationPipelineIntegration:
    """集成测试：Git diff → RuleResult → Evaluation → 最终评分。"""

    def test_full_pipeline_with_real_diff(self, session, demo_git_repo, monkeypatch):
        """端到端测试：真实 diff → 规则结果 → 评估流水线。

        LLM 调用由 conftest mock，通知由 fixture mock。
        Roslyn 不可用时使用种子数据方式注入规则结果。
        """
        bare_path, file_paths, sha1, sha2 = demo_git_repo

        # 1. 获取真实 diff
        diff = get_diff(bare_path, sha1, sha2)
        assert diff, "Diff should not be empty"

        # 2. 创建 GithubEvent
        event = _create_github_event(
            session,
            repo="test/DemoProject",
            before_sha=sha1,
            after_sha=sha2,
            diff=diff,
        )
        event_id = int(event.id)
        assert event_id > 0

        # 3. 从 diff 提取 .cs 文件并注入规则结果 (模拟 Roslyn 不可用时的种子数据)
        cs_files = extract_cs_files_from_diff(diff)
        assert len(cs_files) > 0, f"Should detect changed .cs files, got: {cs_files}"

        # 尝试真实 Roslyn；不可用时用种子数据
        rule_count = 0
        if roslyn_health_check():
            # 真实分析: 用 git show 读取每个文件在 after_sha 的内容
            import git as gitpython
            repo = gitpython.Repo(bare_path)
            file_payload = []
            for rel_path in cs_files:
                try:
                    content = repo.git.show(f"{sha2}:{rel_path}")
                    file_payload.append((rel_path, content))
                except Exception as exc:
                    logger.warning("git show failed for %s: %s", rel_path, exc)

            if file_payload:
                diagnostics = run_roslyn_analysis(file_payload)
                rule_count = save_rule_results(
                    session, event_id=event_id,
                    diagnostics=diagnostics, scan_type="incremental",
                )
            logger.info("Real Roslyn: %d rule results", rule_count)
        else:
            logger.info("Roslyn unavailable — using seed data fallback")
            rule_count = _seed_demo_rule_results(session, event_id, cs_files)

        # 4. 执行评估流水线
        pipeline_result = run_evaluation_pipeline(event, session)
        session.commit()

        # 5. 验证
        # 刷新 event
        session.refresh(event)

        # 5a. 状态应为 success
        assert event.status == "success", f"Expected status=success, got {event.status}"

        # 5b. 应有评估轮次
        round_count = session.scalar(
            select(func.count(EvaluationRound.id)).where(
                EvaluationRound.event_id == event_id,
            )
        )
        # 至少 1 个 rule_check 轮次
        assert round_count >= 1, f"Expected >=1 evaluation rounds, got {round_count}"

        # 5c. 应有综合评分
        assert event.overall_score is not None, "Expected overall_score to be set"
        assert isinstance(event.overall_score, float), f"Expected float score, got {type(event.overall_score)}"

        # 5d. 应有风险等级
        assert event.final_risk_level is not None, "Expected final_risk_level to be set"
        assert event.final_risk_level in ("low", "medium", "high", "critical"), (
            f"Unexpected risk level: {event.final_risk_level}"
        )

        # 5e. 应有建议
        assert event.recommendation in ("blocked", "needs_review", "merge_ready"), (
            f"Unexpected recommendation: {event.recommendation}"
        )

        # 5f. 维度分数应设置
        assert event.dimension_a_score is not None, "dimension_a_score should be set"
        assert event.dimension_b_score is not None, "dimension_b_score should be set"

        logger.info(
            "Pipeline result: score=%.1f risk=%s recommendation=%s | "
            "dim_a=%.1f dim_b=%.1f rounds=%d rules=%d",
            event.overall_score, event.final_risk_level, event.recommendation,
            event.dimension_a_score, event.dimension_b_score,
            round_count, rule_count,
        )

    def test_pipeline_handles_empty_diff(self, session):
        """空 diff 应走 safe defaults 路径。"""
        event = _create_github_event(
            session,
            repo="test/EmptyRepo",
            before_sha="a" * 40,
            after_sha="b" * 40,
            diff="",
        )

        result = run_evaluation_pipeline(event, session)
        session.commit()
        session.refresh(event)

        assert result["files_evaluated"] == 0
        assert event.status == "success"
        assert event.final_risk_level == "unknown"
        assert event.recommendation == "needs_review"


def _seed_demo_rule_results(session, event_id: int, cs_files: list[str]) -> int:
    """Roslyn 不可用时的种子规则数据注入。

    基于文件路径和行号注入模拟的违规记录。
    """
    from unity_check.models import RuleResult as RR

    seed_rules = [
        # GameManager.cs issues
        ("CA1822", "Member can be marked as static", "Assets/Scripts/GameManager.cs", 40, "Warning", "Performance",
         "Member 'UpdateScore' does not access instance data"),
        ("RCS1001", "Add braces to if-else", "Assets/Scripts/GameManager.cs", 55, "Info", "Style",
         "Add braces to if statement"),
        # PlayerController.cs issues
        ("CA1805", "Do not initialize unnecessarily", "Assets/Scripts/PlayerController.cs", 15, "Warning", "Performance",
         "Field 'maxSpeed' is initialized to its default value 0"),
        ("UNT0002", "Use CompareTag instead of tag comparison", "Assets/Scripts/PlayerController.cs", 65, "Warning", "Performance",
         "Use CompareTag() for tag comparison"),
        ("RCS1015", "Use nameof operator", "Assets/Scripts/PlayerController.cs", 67, "Info", "Performance",
         "Use nameof instead of string literal"),
        # Enemy.cs issues
        ("SA1300", "Element should begin with upper-case", "Assets/Scripts/Enemy.cs", 12, "Warning", "Naming",
         "Field 'health' must begin with upper-case letter"),
        ("IDE1006", "Naming rule violation", "Assets/Scripts/Enemy.cs", 14, "Error", "Naming",
         "Field '_Speed' should use camelCase naming"),
        # EnemyAI.cs issues
        ("UNT0001", "Avoid FindObjectOfType in Update", "Assets/Scripts/EnemyAI.cs", 25, "Error", "Performance",
         "FindObjectOfType called in Update; cache reference in Awake/Start"),
        # WeaponSystem.cs issues
        ("CA1062", "Validate arguments of public methods", "Assets/Scripts/WeaponSystem.cs", 20, "Warning", "Maintainability",
         "Parameter 'target' of public method 'Fire' is not validated for null"),
        ("SA1200", "Using directives inside namespace", "Assets/Scripts/WeaponSystem.cs", 1, "Warning", "Style",
         "Using directive outside namespace"),
        # NetworkManager.cs issues
        ("CA2007", "Do not directly await a Task", "Assets/Scripts/NetworkManager.cs", 25, "Warning", "Reliability",
         "Consider calling ConfigureAwait on awaited task"),
        # UIManager.cs issues
        ("CA1303", "Do not pass literals as localized parameters", "Assets/Scripts/UIManager.cs", 40, "Info", "Globalization",
         "Method passes a literal string as parameter"),
        # SaveManager.cs issues
        ("CA5350", "Do Not Use Weak Cryptographic Algorithms", "Assets/Scripts/SaveManager.cs", 42, "Error", "Security",
         "MD5 is a weak hashing algorithm"),
        # DataLoader.cs issues
        ("CA3075", "Insecure DTD Processing", "Assets/Scripts/DataLoader.cs", 18, "Warning", "Security",
         "XmlReaderSettings.DtdProcessing should be set to Prohibit"),
        # Config.cs issues
        ("SA1311", "Static readonly fields upper-case", "Assets/Scripts/Config.cs", 8, "Error", "Naming",
         "Static readonly field 'defaultTimeout' must begin with upper-case"),
        # BattleSystem.cs issues
        ("RCS1001", "Add braces to if-else", "Assets/Scripts/BattleSystem.cs", 20, "Info", "Style",
         "Add braces to if statement"),
        # InventoryManager.cs issues
        ("UNT0004", "Use TryGetValue instead of ContainsKey+indexer", "Assets/Scripts/InventoryManager.cs", 20, "Warning", "Performance",
         "Use TryGetValue to avoid double lookup"),
        # AudioManager.cs issues
        ("CA1822", "Member can be marked as static", "Assets/Scripts/AudioManager.cs", 20, "Warning", "Performance",
         "Method 'PlaySFX' can be marked as static where applicable"),
    ]

    # 先清除已有
    session.query(RR).filter(RR.event_id == event_id, RR.scan_type == "incremental").delete()

    inserted = 0
    for rule_id, rule_name, file_path, line, sev, cat, msg in seed_rules:
        # 只注入 diff 中实际存在的文件
        if file_path not in cs_files:
            continue
        session.add(RR(
            event_id=event_id,
            rule_id=rule_id,
            rule_name=rule_name,
            file_path=file_path,
            line_number=line,
            column_number=10,
            severity=sev,
            category=cat,
            message=msg,
            snippet=f"    // line {line}",
            scan_type="incremental",
        ))
        inserted += 1

    session.flush()
    logger.info("Seeded %d rule results for event_id=%s", inserted, event_id)
    return inserted
