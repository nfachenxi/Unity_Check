---
name: phase1-bugs-and-gaps
description: 第一阶段已知 bug 与功能缺口列表
metadata:
  type: project
---

# 第一阶段已知 Bug 与功能缺口

## 结论
Phase 1 基础链路已搭建完整，但存在 4 个已知 bug 和若干功能缺口。

## Bug（可修复）
1. `config.py:28` — `@lru_cache` 缺少括号，应为 `@lru_cache()`
2. `main.py:41-44` — 使用已弃用的 `@app.on_event("startup")`，应迁移到 `lifespan`
3. Webhook 端点对 GitHub `ping` 事件返回 400，影响 Webhook 配置
4. Celery 任务两次 `db.commit()` 之间无事务保护，崩溃会遗留 "running" 状态

## 功能缺口
- LLM 评估缺少实际代码 diff（当前为盲评）
- 缺少 `.env.example` 模板
- 无 Git clone/fetch/diff 能力
- 零测试覆盖
- 无 Docker Compose 配置

## 适用范围
后续所有开发排期与修复任务分配。
