"""种子演示数据脚本 — 为指定 event 注入预设的 RuleResult 数据。

用途：在演示环境中，为已入库的 GithubEvent 构造一组逼真的静态分析违规记录，
     使三轮评估流水线的 Round 1 有真实数据可汇总，Round 2/3 LLM 评估有上下文可分析。

用法：uv run python scripts/seed_demo_data.py <event_id>
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import select
from unity_check.db import SessionLocal
from unity_check.models import GithubEvent, RuleResult

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 预设的 Unity C# 静态分析违规 — 模拟 Roslyn 分析器输出
# ---------------------------------------------------------------------------
PRESET_DIAGNOSTICS: list[dict] = [
    # ---------- Performance ----------
    {
        "rule_id": "CA1822",
        "rule_name": "Member 'UpdateScore' does not access instance data and can be marked as static",
        "file_path": "Assets/Scripts/GameManager.cs",
        "line_number": 45,
        "column_number": 17,
        "severity": "Warning",
        "category": "Performance",
        "message": "Member 'UpdateScore' does not access instance data and can be marked as static",
        "snippet": "    void UpdateScore() { ... }",
    },
    {
        "rule_id": "CA1805",
        "rule_name": "Do not initialize unnecessarily",
        "file_path": "Assets/Scripts/PlayerController.cs",
        "line_number": 22,
        "column_number": 30,
        "severity": "Warning",
        "category": "Performance",
        "message": "Field 'maxSpeed' is initialized to its default value 0",
        "snippet": "    private float maxSpeed = 0;",
    },
    {
        "rule_id": "RCS1015",
        "rule_name": "Use nameof operator",
        "file_path": "Assets/Scripts/PlayerController.cs",
        "line_number": 78,
        "column_number": 48,
        "severity": "Info",
        "category": "Performance",
        "message": "Use 'nameof' instead of string literal",
        "snippet": '    Debug.Log("maxSpeed");',
    },
    # ---------- Naming ----------
    {
        "rule_id": "SA1300",
        "rule_name": "Element should begin with an upper-case letter",
        "file_path": "Assets/Scripts/Enemy.cs",
        "line_number": 12,
        "column_number": 18,
        "severity": "Warning",
        "category": "Naming",
        "message": "Field 'health' must begin with an upper-case letter",
        "snippet": "    private int health;",
    },
    {
        "rule_id": "IDE1006",
        "rule_name": "Naming rule violation",
        "file_path": "Assets/Scripts/Enemy.cs",
        "line_number": 15,
        "column_number": 22,
        "severity": "Error",
        "category": "Naming",
        "message": "Field '_Speed' should use camelCase naming",
        "snippet": "    private float _Speed;",
    },
    {
        "rule_id": "SA1311",
        "rule_name": "Static readonly fields should begin with upper-case letter",
        "file_path": "Assets/Scripts/Config.cs",
        "line_number": 8,
        "column_number": 30,
        "severity": "Error",
        "category": "Naming",
        "message": "Static readonly field 'defaultTimeout' must begin with upper-case letter",
        "snippet": "    static readonly float defaultTimeout = 5.0f;",
    },
    # ---------- Maintainability ----------
    {
        "rule_id": "CA1062",
        "rule_name": "Validate arguments of public methods",
        "file_path": "Assets/Scripts/WeaponSystem.cs",
        "line_number": 56,
        "column_number": 26,
        "severity": "Warning",
        "category": "Maintainability",
        "message": "Parameter 'target' of public method 'Fire' is not validated for null",
        "snippet": "    public void Fire(GameObject target) { ... }",
    },
    {
        "rule_id": "SA1200",
        "rule_name": "Using directives must be placed inside namespace",
        "file_path": "Assets/Scripts/WeaponSystem.cs",
        "line_number": 1,
        "column_number": 1,
        "severity": "Warning",
        "category": "Style",
        "message": "Using directive should appear within a namespace declaration",
        "snippet": "using UnityEngine;\nusing System.Collections;",
    },
    {
        "rule_id": "RCS1001",
        "rule_name": "Add braces to if-else",
        "file_path": "Assets/Scripts/GameManager.cs",
        "line_number": 102,
        "column_number": 9,
        "severity": "Info",
        "category": "Style",
        "message": "Add braces to if statement",
        "snippet": "    if (isGameOver) return;",
    },
    # ---------- Reliability ----------
    {
        "rule_id": "CA2007",
        "rule_name": "Do not directly await a Task",
        "file_path": "Assets/Scripts/NetworkManager.cs",
        "line_number": 34,
        "column_number": 22,
        "severity": "Warning",
        "category": "Reliability",
        "message": "Consider calling ConfigureAwait on the awaited task",
        "snippet": "    await client.ConnectAsync();",
    },
    {
        "rule_id": "CA1303",
        "rule_name": "Do not pass literals as localized parameters",
        "file_path": "Assets/Scripts/UIManager.cs",
        "line_number": 88,
        "column_number": 16,
        "severity": "Info",
        "category": "Globalization",
        "message": "Method 'SetText' passes a literal string to parameter 'text'",
        "snippet": '    scoreLabel.SetText("Game Over");',
    },
    # ---------- Security ----------
    {
        "rule_id": "CA5350",
        "rule_name": "Do Not Use Weak Cryptographic Algorithms",
        "file_path": "Assets/Scripts/SaveManager.cs",
        "line_number": 42,
        "column_number": 20,
        "severity": "Error",
        "category": "Security",
        "message": "MD5 is a weak hashing algorithm; use SHA256 or stronger",
        "snippet": "    using var md5 = MD5.Create();",
    },
    {
        "rule_id": "CA3075",
        "rule_name": "Insecure DTD Processing",
        "file_path": "Assets/Scripts/DataLoader.cs",
        "line_number": 18,
        "column_number": 14,
        "severity": "Warning",
        "category": "Security",
        "message": "XmlReaderSettings.DtdProcessing should be set to Prohibit",
        "snippet": "    var settings = new XmlReaderSettings();",
    },
    # ---------- Unity-specific (custom rule simulation) ----------
    {
        "rule_id": "UNT0001",
        "rule_name": "Avoid using FindObjectOfType in Update",
        "file_path": "Assets/Scripts/EnemyAI.cs",
        "line_number": 25,
        "column_number": 24,
        "severity": "Error",
        "category": "Performance",
        "message": "FindObjectOfType called in Update; cache the reference in Awake or Start",
        "snippet": "    var player = FindObjectOfType<PlayerController>();",
    },
    {
        "rule_id": "UNT0002",
        "rule_name": "Use CompareTag instead of tag string comparison",
        "file_path": "Assets/Scripts/PlayerController.cs",
        "line_number": 65,
        "column_number": 16,
        "severity": "Warning",
        "category": "Performance",
        "message": "Use CompareTag() instead of comparing tag property to string",
        "snippet": '    if (other.tag == "Enemy") { ... }',
    },
]


def seed_rule_results(event_id: int) -> int:
    """将预设诊断数据写入指定 event 的 rule_results 表。

    返回写入的行数。已存在 incremental scan 数据时先清空再写入。
    """
    db = SessionLocal()
    try:
        event = db.scalar(select(GithubEvent).where(GithubEvent.id == event_id))
        if event is None:
            logger.error("event_id=%s 不存在", event_id)
            return 0

        # 清除已有的 incremental 数据（幂等）
        deleted = (
            db.query(RuleResult)
            .filter(RuleResult.event_id == event_id, RuleResult.scan_type == "incremental")
            .delete()
        )
        if deleted > 0:
            logger.info("已清除 %d 条旧 incremental rule_results", deleted)

        rows = []
        for d in PRESET_DIAGNOSTICS:
            rows.append(
                RuleResult(
                    event_id=event_id,
                    rule_id=d["rule_id"],
                    rule_name=d["rule_name"],
                    file_path=d["file_path"],
                    line_number=d["line_number"],
                    column_number=d["column_number"],
                    severity=d["severity"],
                    category=d["category"],
                    message=d["message"],
                    snippet=d.get("snippet", ""),
                    scan_type="incremental",
                )
            )

        db.add_all(rows)
        db.commit()
        logger.info("已为 event_id=%s 写入 %d 条预设 rule_results", event_id, len(rows))
        return len(rows)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="为指定 GithubEvent 注入演示用 RuleResult 种子数据")
    parser.add_argument("event_id", type=int, help="目标 GithubEvent ID")
    parser.add_argument(
        "--re-evaluate",
        action="store_true",
        help="注入数据后立即触发 /re-evaluate 端点",
    )
    args = parser.parse_args()

    count = seed_rule_results(args.event_id)
    if count == 0:
        sys.exit(1)

    if args.re_evaluate:
        import requests
        resp = requests.post(f"http://localhost:8000/events/{args.event_id}/re-evaluate")
        if resp.status_code == 200:
            logger.info("已触发 event_id=%s 重新评估", args.event_id)
        else:
            logger.warning("重新评估请求失败: %s %s", resp.status_code, resp.text)


if __name__ == "__main__":
    main()
