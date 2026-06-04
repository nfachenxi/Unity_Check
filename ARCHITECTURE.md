# ARCHITECTURE.md — Unity Check 架构文档

## 总架构

```
                      ┌─────────────┐
                      │  GitHub Repo │
                      └──────┬──────┘
                             │ push / pull_request webhook
                             ▼
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI (port 8000)                        │
│                                                              │
│  POST /webhook/github  ──►  github_events 表                 │
│                               │                              │
│                               │ Celery task                  │
│                               ▼                              │
│  celery_app  ──► Redis (broker)  ──►  Celery Worker          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                                             │
                                             ▼
┌──────────────────────────────────────────────────────────────┐
│                    Celery Worker                             │
│                                                              │
│  1. Git Service                                              │
│     ├── 解析 webhook payload (before/after SHA)              │
│     ├── bare clone / fetch (SSH 认证)                        │
│     └── git diff (before..after) ──► diff_content            │
│                                                              │
│  2. Roslyn 规则检测 (Round 1)                                 │
│     ├── diff 中提取 .cs 文件                                 │
│     ├── POST /analyze ──► Roslyn 容器 (port 8080)           │
│     └── rule_results 表                                      │
│                                                              │
│  3. 编排器 (orchestrator.py)                                  │
│     ├── Round 1: 汇总 rule_results                           │
│     ├── Round 2: semantic_review (DeepSeek)                  │
│     └── Round 3: synthesis_summary (DeepSeek)                │
│                                                              │
│  4. 通知服务 (notification_service.py)                       │
│     ├── 阈值判断 (风险等级 + 评分)                            │
│     ├── 企微 Markdown / 飞书卡片构建                         │
│     └── notifications 表                                     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
                                             │
                    ┌────────────────────────┼────────────────────┐
                    ▼                        ▼                    ▼
            ┌──────────┐          ┌──────────────┐      ┌──────────────┐
            │PostgreSQL│          │  Redis        │      │   Roslyn     │
            │  16      │          │  7 (broker)   │      │ .NET 8 API   │
            └──────────┘          └──────────────┘      │  (port 8080) │
                                                        └──────────────┘
                    │
                    ▼
            ┌──────────────┐
            │  Vue3 看板    │
            │  (port 5173)  │
            │              │
            │  /           │  概览看板 (趋势图+风险饼+问题柱+最近事件)
            │  /events     │  事件列表 (分页+筛选)
            │  /events/:id │  事件详情 (Diff+Rules+R1/R2/R3+通知)
            │  /stats      │  统计中心 (评分趋势+高频规则+文件热点)
            └──────────────┘
```

## 三轮评估数据流

```
事件入库
    │
    ▼
┌─────────────────────────────────────────────┐
│  Round 1: rule_check                        │
│  ─────────────────────                      │
│  输入: rule_results 表 (Roslyn 结果)         │
│  输出: 按严重度/类别/文件/规则的聚合摘要      │
│  耗时: <1s (DB 查询)                         │
│  失败处理: 无 — 空结果也是合法输出            │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  Round 2: semantic_review (LLM)             │
│  ─────────────────────────────              │
│  输入: diff + Round 1 聚合摘要 + 事件上下文   │
│  输出: 语义发现列表 [{title, category,       │
│         severity, description, suggestion}]  │
│  耗时: 10-30s (DeepSeek API)                 │
│  失败处理: 3 次指数退避重试; 失败后 R3 仍执行 │
│  截断守卫: diff 超过 8000 字符自动截断        │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  Round 3: synthesis (LLM)                   │
│  ───────────────────────                    │
│  输入: diff + R1 摘要 + R2 发现 + 事件上下文  │
│  输出: {                                    │
│    overall_score: 0-100                     │
│    risk_level: low|medium|high|critical     │
│    executive_summary: 2-4 句摘要             │
│    top_issues: 最多 5 条                     │
│    recommendation: merge_ready|needs_review  │
│                    |blocked                  │
│    action_items: 最多 5 条                   │
│  }                                          │
│  耗时: 10-20s                                │
│  失败处理: 回写安全默认值到事件行              │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
              ┌──────────┐
              │ 通知服务  │
              │ 阈值判断  │
              │ 消息构建  │
              │ 入库      │
              └──────────┘
```

## 数据模型

```
github_events  (主表)
├── id, delivery_id (幂等)
├── event_type (push/pull_request), action
├── repository, before_sha, after_sha
├── clone_path, diff_content, diff_size
├── status, task_id
├── overall_score, final_risk_level, recommendation
├── executive_summary
├── created_at, updated_at
│
├── 1:N ── rule_results        (Round 1 输出)
│           ├── rule_id, rule_name
│           ├── file_path, line_number, column_number
│           ├── severity, category, message, snippet
│           └── scan_type (incremental/baseline)
│
├── 1:N ── evaluation_rounds   (R1/R2/R3 记录)
│           ├── round_number (1/2/3), round_type
│           ├── status, input_summary (JSON), output_data (JSON)
│           ├── score, model_name, tokens_used, duration_ms
│           └── error_message, started_at, completed_at
│
└── 1:N ── notifications       (通知记录)
            ├── channel (wecom/feishu)
            ├── trigger_reason, risk_level
            ├── message_content, webhook_url
            └── status (pending/sent/failed)
```

## 关键设计决策

### 1. Roslyn 容器化 (Docker Sidecar)

**决策**: Roslyn 分析器作为独立 Docker 容器 (ASP.NET Core 8 Minimal API)，Python 通过 HTTP 调用。

**理由**:
- 解耦技术栈：Python 后端不需要 .NET 运行时
- 独立扩展：Roslyn 容器可独立更新/替换
- Docker Compose 统一管理

**演示降级**: NuGet 分析器包在运行时无法加载，演示时通过 `seed_demo_data.py` 预注入模拟 RuleResult。

### 2. 三轮评估流水线

**决策**: Round 1 (规则) → Round 2 (语义) → Round 3 (综合)，每轮独立持久化。

**理由**:
- 每轮结果可独立查询和调试
- Round 2 失败不影响 Round 3 执行
- 支持单独重新评估某一轮
- 每轮有独立的 Token 用量和耗时记录

### 3. 通知与发送分离

**决策**: 通知服务只负责消息构建与入库，实际发送由独立工具平台完成。

**理由**:
- 企业微信/飞书的 API 变更不耦合到核心逻辑
- 支持发送状态回调 (`POST /api/notifications/{id}/send-status`)
- 通知消息可预览、可审计

### 4. 幂等性设计

**决策**: 通过 GitHub `X-GitHub-Delivery` header 实现 webhook 幂等；通过 `scan_type` 字段实现 RuleResult 幂等重写。

**理由**: GitHub 可能在网络超时时重发 webhook，幂等保证不会产生重复评估。

### 5. 种子数据方案

**决策**: 独立的 Python 脚本生成合成历史数据，不依赖真实 Git 仓库。

**理由**:
- 快速展示 2-3 周趋势（无需等 3 周）
- 数据可控可靠（预设趋势：后期质量逐步提升）
- 可复现（`--clean` 重置，`--count` 控制规模）

## 技术选型

| 组件 | 选型 | 备选 |
|------|------|------|
| Web 框架 | FastAPI | Flask, Django |
| 异步任务 | Celery + Redis | RQ, Dramatiq |
| ORM | SQLAlchemy 2.0 (声明式) | Peewee, Tortoise |
| 配置管理 | Pydantic Settings | python-dotenv, dynaconf |
| Git 操作 | GitPython | 直接 subprocess |
| LLM 客户端 | OpenAI Python SDK | httpx 直连 |
| 静态分析 | Roslyn (.NET 8) | 纯 LLM 分析 |
| 前端框架 | Vue 3 + Vite | React, Svelte |
| UI 组件 | Element Plus | Ant Design, Naive UI |
| 图表 | ECharts (vue-echarts) | Chart.js, D3 |
| 数据库 | PostgreSQL 16 | MySQL, SQLite |
| 容器化 | Docker Compose | Kubernetes |
