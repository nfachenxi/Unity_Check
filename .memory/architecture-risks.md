---
name: architecture-risks
description: 当前架构层面的潜在风险与扩展瓶颈
metadata:
  type: project
---

# 架构风险

## 结论
当前单表 `github_events` 混合存储事件和评估结果，第二阶段多轮评估时会成为瓶颈。

## 风险点
1. **单表混存** — `github_events` 同时存 Webhook 事件和 LLM 评估结果，需在 Phase 2 前拆分为 `events` + `evaluations`（1:N）
2. **Roslyn 集成未定义** — Roslyn 是 .NET 生态，如何与 Python 后端集成为未知（容器/子进程/预处理）
3. **"多轮评估"数据流未定义** — 规则结果→语义评估→复核总结 三阶段的数据流转、结果合并、冲突处理未设计
4. **前端指标定义缺失** — 看板的具体维度和计算方式未确定

## 适用范围
Phase 2+ 架构设计与数据模型扩展阶段。
