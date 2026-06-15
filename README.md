# Unity Check

基于大模型与 Git 协作的 Unity 工程代码实时检测与评估系统。

## 核心能力

- **Webhook 接收**：接收 GitHub push / pull_request 事件，自动触发代码评估
- **LLM 双维度评估**：对 diff 中的每个 .cs 文件进行功能/最佳实践 + 安全/性能/健康度评估
- **可视化看板**：Vue3 + Element Plus 前端，提供事件列表、详情、看板与统计

## 技术栈

| 层次 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| 数据库 | SQLite |
| LLM | DeepSeek (OpenAI 兼容 API) |
| 前端 | Vue 3 + Element Plus + ECharts + Vite |

## 快速启动

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY
```

### 2. 启动后端

```bash
uv sync
uv run uvicorn unity_check.main:app --app-dir src --reload
```

后端启动在 `http://localhost:8000`。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端启动在 `http://localhost:5173`，API 代理到后端 8000 端口。

## 架构概览

```
GitHub Push/PR
      │
      ▼
[FastAPI Webhook] ──► [SQLite: github_events]
      │
      ▼
[评估流水线] ──► [Git clone/fetch/diff]
      │
      ├──► [维度A: 功能与最佳实践 (DeepSeek)]
      │
      ├──► [维度B: 安全、性能与健康度 (DeepSeek)]
      │
      ▼
[程序化聚合评分] ──► [Vue3 看板]
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/webhook/github` | POST | GitHub webhook 接收 |
| `/api/events` | GET | 事件分页列表（支持筛选） |
| `/api/events/{id}` | GET | 事件详情 |
| `/api/events/{id}/evaluations` | GET | 评估轮次详情 |
| `/api/events/{id}/assessment` | GET | 综合评估结果 |
| `/api/events/{id}/re-evaluate` | POST | 重新触发评估 |
| `/api/dashboard` | GET | 看板数据（支持 section 参数） |

## 项目结构

```
├── src/unity_check/       # Python 后端核心
│   ├── main.py            # FastAPI 应用 + 全部 API 端点
│   ├── config.py          # 配置管理 (Pydantic Settings)
│   ├── models.py          # ORM 数据模型 (SQLAlchemy)
│   ├── orchestrator.py    # 评估流水线编排
│   ├── llm.py             # LLM 集成 (DeepSeek)
│   ├── git_service.py     # Git clone/fetch/diff
│   ├── rule_service.py    # Diff 解析
│   ├── db.py              # 数据库连接
│   └── migration.py       # 数据库迁移
├── frontend/              # Vue3 + Element Plus 前端
├── tests/                 # pytest 测试
└── Demo/                  # 演示用 Unity C# 项目
```

## 测试

### 运行单元测试

```bash
uv run pytest tests/ -q          # 快速运行全部测试
uv run pytest tests/ -v          # 详细输出
uv run pytest tests/ --cov       # 测试覆盖率报告
uv run pytest tests/test_webhook.py -v  # 仅测试 webhook 模块
```

测试默认 Mock LLM API，无需真实 API Key。

### 手动触发端到端测试

```bash
# 0. 准备本地 Git 仓库（若 GitHub 不可达）
git clone --bare Demo/Unity_Check_Demo repos/github_com_nfachenxi_Unity_Check_Demo.git

# 1. 启动后端
uv run uvicorn unity_check.main:app --app-dir src --reload

# 2. 启动前端（新终端）
cd frontend && npm run dev

# 3. 模拟 GitHub push webhook
cd Demo/Unity_Check_Demo
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{
    "ref": "refs/heads/master",
    "before": "03a00ae",
    "after": "860af7e",
    "repository": {
      "full_name": "demo/Unity_Check_Demo",
      "clone_url": "https://github.com/nfachenxi/Unity_Check_Demo.git"
    },
    "commits": [{}]
  }'
```

等待 20-60 秒完成评估后，打开 `http://localhost:5173` 查看结果。

### 快速验证清单

- [ ] `curl http://localhost:8000/health` 返回 `{"status":"ok"}`
- [ ] `uv run pytest tests/ -q` 全部通过
- [ ] `http://localhost:5173` 前端加载正常
- [ ] `LLM_API_KEY` 已正确配置

详细测试指南见 [TESTING_GUIDE.md](TESTING_GUIDE.md)。
