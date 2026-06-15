# Unity Check — 架构文档

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
│       │                                                     │
│       ▼                                                     │
│  [同步处理]                                                   │
│    1. Git clone/fetch ──► diff_content                      │
│    2. 从 diff 提取 .cs 文件                                  │
│    3. 每文件 × 双维度 LLM 评估                               │
│    4. 程序化聚合 → 更新事件行                                 │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
                     ┌──────────────┐
                     │   SQLite      │
                     │  (数据库文件)   │
                     └──────┬───────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  Vue3 看板    │
                     │  (port 5173)  │
                     │              │
                     │  /           │  概览看板
                     │  /events     │  事件列表
                     │  /events/:id │  事件详情
                     │  /stats      │  统计中心
                     └──────────────┘
```

## 双维度评估数据流

```
事件入库 (diff_content)
    │
    ▼
┌─────────────────────────────────────────────┐
│  提取 .cs 文件                               │
│  从 unified diff 中解析所有 .cs 文件路径      │
│  无 .cs 文件 → 跳过评估                      │
└──────────────────┬──────────────────────────┘
                   │ 每文件循环
                   ▼
┌─────────────────────────────────────────────┐
│  维度A: 功能与最佳实践 (LLM)                  │
│  ─────────────────────────────               │
│  输入: 单文件 diff + 事件上下文               │
│  输出: score(0-100) + summary + findings[]   │
│  耗时: 10-30s (DeepSeek API)                 │
│  失败处理: 3 次指数退避重试, 失败记 0 分       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  维度B: 安全、性能与健康度 (LLM)              │
│  ─────────────────────────────               │
│  输入: 单文件 diff + 事件上下文               │
│  输出: score(0-100) + summary + findings[]   │
│  耗时: 10-30s (DeepSeek API)                 │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  程序化聚合                                   │
│  ───────────                                  │
│  overall_score = 所有维度评分的平均值           │
│  risk_level = 最高严重度发现                   │
│  recommendation = blocked / needs_review      │
│                    / merge_ready               │
│  executive_summary = 中文摘要                  │
└──────────────────────────────────────────────┘
```

## 数据模型

```
github_events  (主表)
├── id, delivery_id (幂等)
├── event_type (push/pull_request), action
├── repository, before_sha, after_sha
├── clone_path, diff_content, diff_size
├── status (queued/running/success/failed)
├── overall_score, final_risk_level, recommendation
├── executive_summary
├── dimension_a_score, dimension_b_score
├── dimension_a_summary, dimension_b_summary
├── error_message
├── created_at, updated_at
│
├── 1:N ── evaluation_rounds   (每文件 × 双维度)
│           ├── round_number (文件序号), round_type
│           ├── file_path
│           ├── status, input_summary (JSON)
│           ├── output_data (JSON: score/summary/findings)
│           ├── score, model_name
│           ├── tokens_used, duration_ms
│           └── error_message, started_at, completed_at
```

## 技术选型

| 组件 | 选型 | 备选 |
|------|------|------|
| Web 框架 | FastAPI | Flask, Django |
| ORM | SQLAlchemy 2.0 | Peewee, Tortoise |
| 数据库 | SQLite | PostgreSQL, MySQL |
| 配置管理 | Pydantic Settings | python-dotenv |
| Git 操作 | GitPython | subprocess |
| LLM | DeepSeek (OpenAI SDK) | 其他 OpenAI 兼容 API |
| 前端框架 | Vue 3 + Vite | React, Svelte |
| UI 组件 | Element Plus | Ant Design |
| 图表 | ECharts | Chart.js |

## 关键设计决策

### 1. 同步处理模式

Webhook 处理采用同步模式（非 Celery 异步），直接在当前请求中完成 Git 操作、LLM 评估和持久化。

**理由**：
- 减少基础设施依赖（无需 Redis / Celery Worker）
- 适合课程作业规模，无需分布式任务队列
- 前端请求可以轮询等待结果

### 2. 双维度评估 + 程序化聚合

每文件的每个维度独立调用 LLM，然后通过程序化规则聚合评分和风险等级。

**理由**：
- 减少 LLM 调用次数（避免三轮评估中的冗余调用）
- 聚合逻辑透明可调试
- 每维度独立持久化，支持重新评估单维度

### 3. SQLite 数据库

使用 SQLite 替代 PostgreSQL，无外部数据库依赖。

**理由**：
- 零配置启动，无需 Docker
- 适合单一服务器/开发环境
- 开发与演示无需额外部署

### 4. 幂等性设计

通过 GitHub `X-GitHub-Delivery` header 实现 webhook 幂等。

**理由**：GitHub 可能在网络超时时重发 webhook，幂等保证不会产生重复事件。
