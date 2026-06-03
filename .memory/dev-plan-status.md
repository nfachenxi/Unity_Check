---
name: dev-plan-status
description: 整体开发计划当前进展状态（修订版）
metadata:
  type: project
---

# 开发计划进展状态（修订版）

## 结论
Phase 1a 和 Phase 1b 均已完成。下一阶段为 Phase 2（Roslyn 规则检测）。

## Phase 1a ✅ 已完成
- @lru_cache 修复、lifespan 迁移、ping 事件、事务原子性
- .env.example、GIT_SSH_KEY_PATH 配置
- 基础测试（config/models/webhook/tasks）

## Phase 1b ✅ 已完成（2026-06-02）
- **数据模型**：GithubEvent 新增 after_sha、before_sha、clone_path、diff_content、diff_size 5 列
- **Git Service**：`src/unity_check/git_service.py` — bare repo clone/fetch/diff + SHA/URL 提取
- **LLM 增强**：`llm.py` 新增 diff_content 参数、8000 字符截断守卫、Unity 专项分析 prompt
- **任务重构**：`tasks.py` 集成 git 操作，支持 clone_url 自动解析（payload → 配置项 fallback）
- **Webhook 增强**：`main.py` 提取 before/after SHA + 新增 `GET /events/{event_id}`
- **Docker**：Dockerfile + docker-compose.yml（api + worker，复用远端 DB/Redis）
- **测试**：65 个测试全部通过，覆盖率 86%（git_service 88%）

## 后续
Phase 2：Roslyn 容器化规则检测。

## 适用范围
所有任务排期与进展追踪。
