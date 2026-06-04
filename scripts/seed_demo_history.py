"""种子历史数据脚本 — 批量生成 2-3 周的合成 GithubEvent + RuleResult + EvaluationRound.

用途：为演示/答辩创建逼真的历史数据，使前端看板趋势图、饼图、柱状图有真实数据展示。

用法：uv run python scripts/seed_demo_history.py [--count 50] [--days 21]
"""

import argparse
import logging
import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from unity_check.db import SessionLocal
from unity_check.models import EvaluationRound, GithubEvent, RuleResult

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 合成数据素材
# ---------------------------------------------------------------------------
REPOS = ["nfachenxi/ElementWar", "team/UnityGame", "org/ClientApp"]
EVENT_TYPES = ["push", "push", "push", "pull_request"]  # 3:1 ratio
BRANCHES = ["refs/heads/main", "refs/heads/develop", "refs/heads/feature/combat"]
FILE_POOL = [
    "Assets/Scripts/GameManager.cs",
    "Assets/Scripts/PlayerController.cs",
    "Assets/Scripts/Enemy.cs",
    "Assets/Scripts/EnemyAI.cs",
    "Assets/Scripts/WeaponSystem.cs",
    "Assets/Scripts/NetworkManager.cs",
    "Assets/Scripts/UIManager.cs",
    "Assets/Scripts/SaveManager.cs",
    "Assets/Scripts/DataLoader.cs",
    "Assets/Scripts/Config.cs",
    "Assets/Scripts/BattleSystem.cs",
    "Assets/Scripts/InventoryManager.cs",
    "Assets/Scripts/SkillHandler.cs",
    "Assets/Scripts/AudioManager.cs",
    "Assets/Scripts/CameraController.cs",
]

RULE_POOL: list[dict] = [
    {"rule_id": "CA1822", "rule_name": "Member can be marked as static", "severity": "Warning", "category": "Performance"},
    {"rule_id": "CA1805", "rule_name": "Do not initialize unnecessarily", "severity": "Warning", "category": "Performance"},
    {"rule_id": "RCS1015", "rule_name": "Use nameof operator", "severity": "Info", "category": "Performance"},
    {"rule_id": "UNT0001", "rule_name": "Avoid FindObjectOfType in Update", "severity": "Error", "category": "Performance"},
    {"rule_id": "UNT0002", "rule_name": "Use CompareTag instead of tag comparison", "severity": "Warning", "category": "Performance"},
    {"rule_id": "SA1300", "rule_name": "Element should begin with upper-case", "severity": "Warning", "category": "Naming"},
    {"rule_id": "IDE1006", "rule_name": "Naming rule violation", "severity": "Error", "category": "Naming"},
    {"rule_id": "SA1311", "rule_name": "Static readonly fields upper-case", "severity": "Error", "category": "Naming"},
    {"rule_id": "CA1062", "rule_name": "Validate arguments of public methods", "severity": "Warning", "category": "Maintainability"},
    {"rule_id": "SA1200", "rule_name": "Using directives inside namespace", "severity": "Warning", "category": "Style"},
    {"rule_id": "RCS1001", "rule_name": "Add braces to if-else", "severity": "Info", "category": "Style"},
    {"rule_id": "CA2007", "rule_name": "Do not directly await a Task", "severity": "Warning", "category": "Reliability"},
    {"rule_id": "CA5350", "rule_name": "Do Not Use Weak Cryptographic Algorithms", "severity": "Error", "category": "Security"},
    {"rule_id": "CA3075", "rule_name": "Insecure DTD Processing", "severity": "Warning", "category": "Security"},
]

SEMANTIC_FINDING_POOL: list[dict] = [
    {"title": "GameManager 单例模式缺少线程安全", "category": "architecture", "severity": "high",
     "description": "GameManager 使用简单的懒汉单例但未考虑多线程场景，可能在异步加载时产生多个实例。",
     "suggestion": "使用 Lazy<T> 或双重检查锁定模式。", "file": "Assets/Scripts/GameManager.cs", "line_hint": "line 15-25"},
    {"title": "Update 中重复调用 GetComponent", "category": "performance", "severity": "high",
     "description": "PlayerController.Update 中每帧调用 GetComponent<Rigidbody>()，产生不必要的 GC 分配。",
     "suggestion": "在 Awake 中缓存引用到成员变量。", "file": "Assets/Scripts/PlayerController.cs", "line_hint": "line 45"},
    {"title": "协程未在 OnDisable 中停止", "category": "unity_anti_pattern", "severity": "medium",
     "description": "EnemyAI 在 OnEnable 中启动协程，但 OnDisable 中未 StopCoroutine，可能导致对象禁用后协程继续运行。",
     "suggestion": "在 OnDisable 中调用 StopAllCoroutines。", "file": "Assets/Scripts/EnemyAI.cs", "line_hint": "line 30-42"},
    {"title": "字符串拼接用于日志输出", "category": "performance", "severity": "low",
     "description": "多处 Debug.Log 使用字符串拼接，在 Release 构建中仍会产生 GC 分配。",
     "suggestion": "使用 Debug.LogFormat 或条件编译。", "file": "Assets/Scripts/WeaponSystem.cs"},
    {"title": "网络消息未做长度校验", "category": "security", "severity": "high",
     "description": "NetworkManager 反序列化网络消息时未验证消息长度，可能导致缓冲区溢出。",
     "suggestion": "添加最大消息长度检查和 try-catch 错误处理。", "file": "Assets/Scripts/NetworkManager.cs", "line_hint": "line 55-62"},
    {"title": "Animation 组件使用字符串哈希查找", "category": "performance", "severity": "medium",
     "description": "多处使用 animator.SetTrigger(\"Attack\") 字符串重载，每帧产生字符串到哈希的转换。",
     "suggestion": "在 Awake 中预先计算 Animator.StringToHash。", "file": "Assets/Scripts/PlayerController.cs", "line_hint": "line 70"},
    {"title": "缺少空引用检查", "category": "maintainability", "severity": "medium",
     "description": "UIManager 直接访问可能为 null 的 scoreLabel 字段，在场景加载顺序异常时会 NRE。",
     "suggestion": "添加 null 检查或使用 [Required] 属性。", "file": "Assets/Scripts/UIManager.cs", "line_hint": "line 88"},
]

# ---------------------------------------------------------------------------
# 生成逻辑
# ---------------------------------------------------------------------------


def _random_sha() -> str:
    return "%040x" % random.getrandbits(160)


def _random_diff(num_files: int = 2) -> str:
    files = random.sample(FILE_POOL, min(num_files, len(FILE_POOL)))
    parts = []
    i = 0
    for f in files:
        i += 1
        parts.append(
            f"diff --git a/{f} b/{f}\n"
            f"index {random.randint(100000,999999)}..{random.randint(100000,999999)} 100644\n"
            f"--- a/{f}\n"
            f"+++ b/{f}\n"
            f"@@ -{random.randint(1,50)},{random.randint(3,15)}"
            f" +{random.randint(1,50)},{random.randint(3,15)} @@\n"
            f"+// Changed line {i}\n"
            f"+ var newField = {random.randint(0,100)};\n"
            f"-// Old line {i}\n"
            f"- var oldField = {random.randint(0,100)};\n"
        )
    return "\n".join(parts)


def _generate_rule_results(event_id: int) -> list[RuleResult]:
    """为单个事件随机生成 0-8 条规则违规。"""
    count = random.choices([0, 1, 2, 3, 4, 5, 6, 7, 8], weights=[10, 15, 20, 20, 15, 10, 5, 3, 2])[0]
    if count == 0:
        return []

    chosen = random.choices(RULE_POOL, k=min(count, len(RULE_POOL)))
    rows = []
    for r in chosen:
        f = random.choice(FILE_POOL)
        rows.append(RuleResult(
            event_id=event_id,
            rule_id=r["rule_id"],
            rule_name=r["rule_name"],
            file_path=f,
            line_number=random.randint(1, 200),
            column_number=random.randint(1, 60),
            severity=r["severity"],
            category=r["category"],
            message=f"{r['rule_name']} in {f.split('/')[-1]}",
            snippet=f"    // line {random.randint(1,200)}",
            scan_type="incremental",
        ))
    return rows


def _generate_evaluation_rounds(event_id: int, overall_score: float | None,
                                 final_risk_level: str | None) -> list[EvaluationRound]:
    """为单个事件生成 R1/R2/R3 评估轮次记录。"""
    now = datetime.now(timezone.utc)
    rounds: list[EvaluationRound] = []

    # R1: rule_check — success
    rounds.append(EvaluationRound(
        event_id=event_id, round_number=1, round_type="rule_check",
        status="success", score=None, model_name="roslyn-docker",
        tokens_used=0, duration_ms=random.randint(200, 1500),
        input_summary={"diff_size": random.randint(500, 5000)},
        output_data={"total": random.randint(0, 8), "note": "seeded history"},
        started_at=now - timedelta(seconds=random.randint(30, 60)),
        completed_at=now - timedelta(seconds=random.randint(25, 55)),
    ))

    # R2: semantic_review — 90% success
    r2_success = random.random() < 0.9
    if r2_success:
        findings = random.sample(SEMANTIC_FINDING_POOL, min(random.randint(1, 4), len(SEMANTIC_FINDING_POOL)))
        rounds.append(EvaluationRound(
            event_id=event_id, round_number=2, round_type="semantic_review",
            status="success", score=None, model_name="deepseek-chat",
            tokens_used=random.randint(2000, 8000), duration_ms=random.randint(8000, 25000),
            output_data={"findings": findings},
            started_at=now - timedelta(seconds=random.randint(25, 55)),
            completed_at=now - timedelta(seconds=random.randint(5, 20)),
        ))
    else:
        rounds.append(EvaluationRound(
            event_id=event_id, round_number=2, round_type="semantic_review",
            status="failed", model_name="deepseek-chat",
            tokens_used=0, duration_ms=random.randint(3000, 10000),
            error_message="Simulated API timeout",
            started_at=now - timedelta(seconds=random.randint(25, 55)),
            completed_at=now - timedelta(seconds=random.randint(5, 20)),
        ))

    # R3: synthesis
    rounds.append(EvaluationRound(
        event_id=event_id, round_number=3, round_type="synthesis",
        status="success", score=overall_score, model_name="deepseek-chat",
        tokens_used=random.randint(3000, 12000), duration_ms=random.randint(5000, 18000),
        output_data={
            "overall_score": overall_score,
            "risk_level": final_risk_level,
            "executive_summary": f"Synthesized assessment for event {event_id}.",
            "top_issues": [{"title": "Seeded issue", "severity": final_risk_level or "low", "source": "semantic_review"}],
            "recommendation": "needs_review" if (overall_score or 100) < 70 else "merge_ready",
            "action_items": [{"action": "Review seeded findings", "priority": "medium"}],
        },
        started_at=now - timedelta(seconds=random.randint(5, 20)),
        completed_at=now,
    ))

    return rounds


def _score_for_risk(risk: str) -> float:
    """Return a plausible score for a given risk level."""
    ranges = {
        "critical": (10, 35),
        "high": (30, 55),
        "medium": (50, 75),
        "low": (70, 98),
    }
    lo, hi = ranges.get(risk, (50, 80))
    return round(random.uniform(lo, hi), 1)


def _risk_for_day_offset(day: int, total_days: int) -> str:
    """Later days should tend toward lower risk (simulating improvement over time)."""
    progress = day / max(total_days, 1)  # 0 → 1 as time passes
    # Stronger bias toward low risk in later days
    if random.random() < progress * 0.4:
        return "low"
    return random.choices(
        ["critical", "high", "medium", "low"],
        weights=[10, 25, 35, 30],
    )[0]


def seed_history(count: int = 50, days: int = 21) -> int:
    """生成 *count* 条历史事件，均匀分布在 *days* 天内。

    返回生成的 event 数量。
    """
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        created = 0

        for i in range(count):
            day_offset = (i * days) // count
            ts = now - timedelta(days=days - day_offset, hours=random.randint(0, 23), minutes=random.randint(0, 59))

            repo = random.choice(REPOS)
            event_type = random.choice(EVENT_TYPES)
            after_sha = _random_sha()
            before_sha = _random_sha() if event_type == "push" else _random_sha()

            risk = _risk_for_day_offset(day_offset, days)
            score = _score_for_risk(risk)

            diff = _random_diff(random.randint(1, 5))
            diff_size = len(diff.encode("utf-8"))

            event = GithubEvent(
                delivery_id=f"seed-history-{i}-{random.randint(1000,9999)}",
                event_type=event_type,
                action="opened" if event_type == "pull_request" else None,
                repository=repo,
                after_sha=after_sha,
                before_sha=before_sha,
                clone_path=f"./repos/{repo.replace('/', '_')}.git",
                diff_content=diff,
                diff_size=diff_size,
                payload={
                    "ref": random.choice(BRANCHES),
                    "commits": [{}] * random.randint(1, 5),
                    "repository": {"full_name": repo},
                },
                status="success",
                risk_level=risk,
                overall_score=score,
                final_risk_level=risk,
                recommendation="merge_ready" if risk == "low" else "needs_review",
                executive_summary=(
                    f"本次提交包含 C# 代码变更，经三轮评估综合评分为 {score}，"
                    f"风险等级 {risk.upper()}。建议{'通过' if risk == 'low' else '审查后合并'}。"
                ),
                created_at=ts,
                updated_at=ts,
            )
            db.add(event)
            db.flush()

            # Rule results
            rules = _generate_rule_results(int(event.id))
            if rules:
                db.add_all(rules)

            # Evaluation rounds
            rounds = _generate_evaluation_rounds(int(event.id), score, risk)
            db.add_all(rounds)

            db.flush()
            created += 1

            if (i + 1) % 10 == 0:
                logger.info("已生成 %d/%d 条事件...", i + 1, count)

        db.commit()
        logger.info("种子数据生成完成：共 %d 条历史事件，%d 天跨度", created, days)
        return created
    except Exception as exc:
        db.rollback()
        logger.exception("种子数据生成失败: %s", exc)
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 Unity Check 演示用历史事件种子数据")
    parser.add_argument("--count", type=int, default=50, help="生成的事件数 (默认 50)")
    parser.add_argument("--days", type=int, default=21, help="时间跨度天数 (默认 21)")
    parser.add_argument("--clean", action="store_true", help="先清除所有已有种子数据再生成")
    args = parser.parse_args()

    if args.clean:
        db = SessionLocal()
        try:
            from sqlalchemy import delete
            db.execute(delete(EvaluationRound).where(EvaluationRound.event_id.in_(
                select(GithubEvent.id).where(GithubEvent.delivery_id.like("seed-history-%"))
            )))
            db.execute(delete(RuleResult).where(RuleResult.event_id.in_(
                select(GithubEvent.id).where(GithubEvent.delivery_id.like("seed-history-%"))
            )))
            db.execute(delete(GithubEvent).where(GithubEvent.delivery_id.like("seed-history-%")))
            db.commit()
            logger.info("已清除旧种子数据")
        finally:
            db.close()

    count = seed_history(args.count, args.days)
    if count == 0:
        sys.exit(1)

    logger.info("种子数据已就绪，可启动前端查看: http://localhost:5173")


if __name__ == "__main__":
    main()
