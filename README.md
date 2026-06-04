# Unity Check

基于大模型与 Git 协作的 Unity 工程代码实时检测、多轮评估与智能通知系统。

## 核心能力

- **Webhook 接收**：接收 GitHub push / pull_request 事件，自动触发代码评估流水线
- **静态规则检测**：通过 Roslyn 分析器对 C# 代码进行规则级静态扫描
- **多轮 LLM 评估**：三轮评估流水线（规则检测 → 语义审查 → 综合评分），利用 DeepSeek 大模型深度分析代码质量
- **智能通知**：根据风险等级和评分自动生成企业微信 / 飞书通知消息
- **可视化看板**：Vue3 + ECharts 前端，提供提交趋势、风险分布、问题热点的实时展示

## 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI + Celery |
| 数据库 | PostgreSQL 16 |
| 缓存/队列 | Redis 7 |
| 静态分析 | Roslyn (.NET 8, Docker 化) |
| LLM | DeepSeek (OpenAI 兼容 API) |
| 前端 | Vue 3 + Element Plus + ECharts + Vite |
| 容器化 | Docker Compose (5 服务) |

## 快速启动

### 环境要求

- Docker & Docker Compose
- Python 3.11+（本地开发）
- Node.js 18+（前端开发）

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY、GITHUB_REMOTE_REPO 等实际值
```

### 2. 启动全部服务

```bash
docker compose up -d
```

启动后可用服务：

| 服务 | 端口 |
|------|------|
| FastAPI 后端 | 8000 |
| Roslyn 分析器 | 8080 |
| PostgreSQL | 5432 |
| Redis | 6379 |

### 3. 生成演示种子数据

```bash
# 生成 50 条历史事件，跨度 21 天
uv run python scripts/seed_demo_history.py --count 50 --days 21

# 或清除旧数据后重新生成
uv run python scripts/seed_demo_history.py --count 50 --days 21 --clean
```

### 4. 启动前端（开发模式）

```bash
cd frontend
npm install
npm run dev
```

前端启动在 `http://localhost:5173`，API 代理到后端 8000 端口。

### 5. 模拟 webhook 触发评估

```bash
# 使用 Demo 仓库的两次提交 SHA 模拟 push 事件
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{
    "ref": "refs/heads/master",
    "before": "03a00ae...",
    "after": "860af7e...",
    "repository": {"full_name": "nfachenxi/Unity_Check_Demo", "ssh_url": "git@github.com:nfachenxi/Unity_Check_Demo.git"},
    "commits": [{}]
  }'
```

### 6. 注入演示用规则数据

```bash
# 对刚创建的事件注入 15 条预设规则违规
uv run python scripts/seed_demo_data.py <event_id>

# 注入后立即触发重新评估
uv run python scripts/seed_demo_data.py <event_id> --re-evaluate
```

## 架构概览

```
GitHub Push/PR
      │
      ▼
[FastAPI Webhook] ──► [PostgreSQL: github_events]
      │
      ▼
[Celery Worker (Redis)]
      │
      ▼
[编排器] ──┬──► [Git Service: clone/fetch/diff]
           │
           ├──► [Round 1: Roslyn 规则检测]
           │
           ├──► [Round 2: LLM 语义审查 (DeepSeek)]
           │
           ├──► [Round 3: LLM 综合评分 (DeepSeek)]
           │
           ▼
      [通知服务] ──► [企业微信 / 飞书]
           │
           ▼
      [Vue3 看板] ◄── [FastAPI 查询 API]
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/webhook/github` | POST | GitHub webhook 接收 |
| `/api/events` | GET | 事件分页列表（支持筛选） |
| `/events/{id}` | GET | 事件详情（含 diff） |
| `/events/{id}/rules` | GET | 规则检测结果 |
| `/events/{id}/evaluations` | GET | 三轮评估详情 |
| `/events/{id}/assessment` | GET | 综合评估结果 |
| `/events/{id}/re-evaluate` | POST | 重新触发评估 |
| `/api/dashboard/summary` | GET | 看板概览数据 |
| `/api/dashboard/trends` | GET | 趋势数据 |
| `/api/dashboard/issue-distribution` | GET | 问题分布 |
| `/api/stats/scores` | GET | 评分统计 |
| `/api/stats/hotspots` | GET | 文件热点 |
| `/api/notifications` | GET | 通知列表 |
| `/api/notifications/{id}/send-status` | POST | 通知状态回调 |

## 项目结构

```
├── src/unity_check/       # Python 后端核心
│   ├── main.py            # FastAPI 应用 + 全部 API 端点
│   ├── config.py           # 配置管理 (Pydantic Settings)
│   ├── models.py           # ORM 数据模型 (SQLAlchemy)
│   ├── orchestrator.py     # 三轮评估编排引擎
│   ├── llm.py              # LLM 集成 (DeepSeek)
│   ├── tasks.py            # Celery 异步任务
│   ├── git_service.py      # Git clone/fetch/diff
│   ├── rule_service.py     # Roslyn 分析器 HTTP 客户端
│   ├── notification_service.py  # 通知构建与入库
│   ├── db.py               # 数据库连接
│   └── celery_app.py       # Celery 配置
├── roslyn-analyzer/        # Roslyn .NET 8 分析器项目 (Docker)
├── frontend/               # Vue3 + Element Plus 前端
├── scripts/
│   ├── seed_demo_data.py       # 单事件规则数据注入
│   └── seed_demo_history.py    # 批量历史种子数据生成
├── tests/                  # pytest 测试 (130 项)
├── docker-compose.yml      # 5 服务 Docker 编排
└── Demo/                   # 演示用 Unity C# 项目
```

## 开发

```bash
# 安装依赖
uv sync

# 运行测试 (130 项)
uv run pytest tests/ -q

# 启动开发服务器
uv run uvicorn unity_check.main:app --app-dir src --reload

# 启动 Celery Worker
uv run celery -A unity_check.celery_app:celery_app worker --loglevel=INFO
```
