# Unity Check — 综合测试指南

本文档覆盖从单元测试到端到端全链路验证的完整测试流程，确保系统各模块功能正常。

---

## 目录

1. [环境准备与配置验证](#1-环境准备与配置验证)
2. [阶段一：单元测试](#2-阶段一单元测试)
3. [阶段二：启动全栈服务](#3-阶段二启动全栈服务)
4. [阶段三：模拟 Webhook 触发评估](#4-阶段三模拟-webhook-触发评估)
5. [阶段四：前端验证](#5-阶段四前端验证)
6. [阶段五：高级测试场景](#6-阶段五高级测试场景)
7. [常见问题排查](#7-常见问题排查)
8. [API 速查表](#8-api-速查表)

---

## 1. 环境准备与配置验证

### 1.1 前置依赖

| 工具 | 最低版本 | 验证命令 |
|------|---------|---------|
| Python | 3.13 | `python --version` |
| uv | 最新 | `uv --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |

### 1.2 配置检查

```powershell
# 复制环境变量模板（首次）
cp .env.example .env

# 编辑 .env，至少配置 LLM_API_KEY
notepad .env
```

关键配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_API_KEY` | **必填** DeepSeek API 密钥 | `sk-CHANGEME` |
| `LLM_BASE_URL` | LLM API 地址（可换其他兼容 API） | `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 | `deepseek-chat` |
| `APP_PORT` | 后端监听端口 | `8000` |
| `DATABASE_URL` | SQLite 数据库路径 | `sqlite:///./data/unity_check.db` |

> **注意**：运行单元测试不需要配置 `LLM_API_KEY`（默认 Mock），但端到端测试需要真实的 API Key。

### 1.3 安装依赖

```powershell
# Python 依赖（含开发依赖）
uv sync

# 前端依赖
cd frontend
npm install
cd ..
```

---

## 2. 阶段一：单元测试

### 2.1 运行全部测试

```powershell
# 进入项目根目录（已在则跳过）
cd F:\work\Project

# 运行全部测试（简洁模式）
uv run pytest tests/ -q

# 详细模式（显示每个测试名称和结果）
uv run pytest tests/ -v
```

预期输出示例：
```
.......ss..s....ss..                                        [100%]
26 passed, 4 skipped in 0.58s
```

### 2.2 单模块测试

```powershell
# Webhook 端点与 API 测试
uv run pytest tests/test_webhook.py -v

# 评估流水线编排测试
uv run pytest tests/test_orchestrator.py -v

# LLM 调用与重试机制测试
uv run pytest tests/test_llm_v2.py -v

# Git 服务测试
uv run pytest tests/test_git_service.py -v

# 配置管理测试
uv run pytest tests/test_config.py -v
```

### 2.3 测试覆盖率报告

```powershell
uv run pytest tests/ --cov=src/unity_check --cov-report=term
```

覆盖率报告会显示每个源文件的覆盖百分比。当前核心模块预期覆盖率：

| 文件 | 预期覆盖率 | 说明 |
|------|-----------|------|
| `main.py` | ~70%+ | API 端点（部分未覆盖筛选组合） |
| `orchestrator.py` | ~85%+ | 评估流水线 |
| `llm.py` | ~90%+ | LLM 调用与重试 |
| `git_service.py` | ~80%+ | Git 操作 |
| `config.py` | ~95%+ | 配置管理 |
| `rule_service.py` | ~60%+ | Diff 解析（间接覆盖） |

### 2.4 测试内容速览

| 测试文件 | 测试场景 | 数量 |
|---------|---------|------|
| `test_webhook.py` | ping/push/pull_request 端点、幂等性、事件详情 API、参数校验 | ~10 |
| `test_orchestrator.py` | 评估流水线、多文件处理、空 diff、无 .cs 文件、聚合评分 | ~9 |
| `test_llm_v2.py` | LLM 成功响应、JSON 解析、API 错误、重试耗尽、无 API Key | ~10 |
| `test_git_service.py` | URL 解析、SHA 提取、本地 bare repo 的 diff 获取 | ~8 |
| `test_config.py` | 配置加载、默认值、缓存 | ~3 |

> **注意**：测试使用 `monkeypatch` 将 LLM 调用替换为 Mock（返回 85 分 + 预设 findings），不消耗真实 API 额度。

---

## 3. 阶段二：启动全栈服务

### 3.1 启动后端

方法一：一键启动（仅后端）
```powershell
# 在项目根目录
.\start_all.ps1
```

方法二：手动启动
```powershell
uv run uvicorn unity_check.main:app --app-dir src --reload
```

后端默认监听 `http://localhost:8000`。

### 3.2 验证后端

```powershell
curl http://localhost:8000/health
```

预期返回：
```json
{"status":"ok"}
```

### 3.3 启动前端

```powershell
cd frontend
npm run dev
```

前端默认监听 `http://localhost:5173`，API 请求自动代理到 `localhost:8000`。

### 3.4 验证前端

打开浏览器访问 `http://localhost:5173`，应看到：
- 左侧导航栏（概览看板 / 事件列表 / 统计中心）
- 右侧主区域显示数据加载状态或空状态提示
- 控制台无 API 请求报错

---

## 4. 阶段三：模拟 Webhook 触发评估

### 4.1 准备本地 Git 仓库

由于测试环境可能无法访问外部 GitHub，需要先从 Demo 项目创建本地 bare repo：

```powershell
# 从 Demo 项目创建本地 bare repo（路径与 _repo_name_from_url 计算结果一致）
cd F:\work\Project
git clone --bare Demo/Unity_Check_Demo repos/github_com_nfachenxi_Unity_Check_Demo.git
```

这条命令创建完成后，后续的 webhook 模拟请求就可以使用 Demo 项目的 commit SHA 进行测试。

### 4.2 Push 事件（全部文件）

模拟一次 push，触发对全部 18 个 .cs 文件的评估：

```powershell
cd F:\work\Project\Demo\Unity_Check_Demo
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: push" `
  -H "X-GitHub-Delivery: demo-push-001" `
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

**预期行为**：
1. 后端日志输出 Git clone 或 fetch 信息
2. 提取 diff，发现 18 个 .cs 文件
3. 逐个文件进行双维度 LLM 评估（共 36 次 LLM 调用）
4. 聚合评分，更新事件状态
5. 返回 `{"status":"success","event_id":"1"}`

**耗时**：约 30-90 秒（取决于 LLM API 响应速度）。

> **注意**：这里 `clone_url` 使用的是公开 HTTPS 地址。如果网络受限，可使用本地文件路径或配置 SSH。

### 4.3 观察后端日志

启动后端的终端会输出类似以下日志：

```
INFO | unity_check.main | Processing event 1: push demo/Unity_Check_Demo
INFO | unity_check.git_service | Cloning bare repo from https://github.com/nfachenxi/Unity_Check_Demo.git
INFO | unity_check.orchestrator | Starting evaluation pipeline for event 1
INFO | unity_check.orchestrator | Evaluating file 1/18: Assets/Scripts/PlayerController.cs (dimension A)
INFO | unity_check.orchestrator | Evaluating file 1/18: Assets/Scripts/PlayerController.cs (dimension B)
INFO | unity_check.orchestrator | Evaluating file 2/18: Assets/Scripts/GameManager.cs (dimension A)
...
INFO | unity_check.orchestrator | Pipeline complete for event 1: score=72.3, risk=medium
```

### 4.4 Pull Request 事件

```powershell
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: pull_request" `
  -H "X-GitHub-Delivery: demo-pr-001" `
  -d '{
    "action": "opened",
    "pull_request": {
      "base": { "sha": "03a00ae" },
      "head": { "sha": "860af7e" }
    },
    "repository": {
      "full_name": "demo/Unity_Check_Demo",
      "clone_url": "https://github.com/nfachenxi/Unity_Check_Demo.git"
    }
  }'
```

### 4.5 幂等性测试

使用相同的 `X-GitHub-Delivery` 再次发送请求：

```powershell
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: push" `
  -H "X-GitHub-Delivery: demo-push-001" `
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

预期：秒级返回 `{"status":"accepted","event_id":"1"}`，不重复执行评估。

### 4.6 重评估（Re-evaluate）

对已存在的事件重新触发评估：

```powershell
curl -X POST http://localhost:8000/api/events/1/re-evaluate
```

预期：删除旧评估轮次，重新运行完整流水线，返回 `{"status":"success","event_id":1,"message":"Re-evaluation completed."}`。

---

## 5. 阶段四：前端验证

### 5.1 概览看板（首页 `http://localhost:5173/`）

触发模拟 webhook 并完成评估后，刷新看板页面，验证：

| 组件 | 预期内容 |
|------|---------|
| 总提交数 | 显示 `1` |
| 平均评分 | 显示 LLM 给出的综合评分（如 `72.3`） |
| 高风险事件数 | 根据 risk_level 显示 |
| 近 30 天趋势图 | 画布渲染正常，有数据点 |
| 风险分布饼图 | 环形图显示风险占比 |
| 问题类型柱状图 | 显示各类 findings 数量 |
| 最近事件表格 | 显示刚触发的事件行 |

### 5.2 事件列表（`http://localhost:5173/events`）

| 功能 | 验证方式 |
|------|---------|
| 分页列表 | 事件以表格形式展示，显示类型/仓库/评分/风险/摘要 |
| 筛选 | 使用类型、风险等级、状态下拉框筛选 |
| 点击跳转 | 点击事件行跳转到详情页 |

### 5.3 事件详情（`http://localhost:5173/events/1`）

| Tab | 验证内容 |
|-----|---------|
| 维度 A | 每个文件的评分、摘要、findings 列表（分类/严重度/描述） |
| 维度 B | 同上，侧重安全与性能维度 |
| Diff 视图 | 显示原始 git diff 内容 |
| 评估摘要 | 综合评分环形图 + 双维度分数 + 执行摘要 |

### 5.4 统计中心（`http://localhost:5173/stats`）

| 组件 | 预期内容 |
|------|---------|
| 评分趋势图 | 折线图显示评分历史 |
| 文件热点 TOP10 | 柱状图显示被评估最多的文件 |
| 统计卡片 | 总事件数、最高/最低评分 |
| 日期选择器 | 可切换日期范围 |

---

## 6. 阶段五：高级测试场景

### 6.1 无 .cs 文件的事件

模拟一次 push 不包含任何 .cs 文件：

```powershell
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: push" `
  -H "X-GitHub-Delivery: test-no-cs" `
  -d '{
    "ref": "refs/heads/master",
    "before": "860af7e",
    "after": "860af7e",
    "repository": {
      "full_name": "demo/Unity_Check_Demo",
      "clone_url": "https://github.com/nfachenxi/Unity_Check_Demo.git"
    },
    "commits": [{}]
  }'
```

预期：状态为 `success`，`final_risk_level` 为 `unknown`，点评分为空。

### 6.2 Ping 事件

```powershell
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: ping" `
  -d '{}'
```

预期：返回 `{"status":"ok"}`。

### 6.3 错误事件类型

```powershell
curl -X POST http://localhost:8000/webhook/github `
  -H "Content-Type: application/json" `
  -H "X-GitHub-Event: issues" `
  -d '{}'
```

预期：返回 400，内容 `{"detail":"Only push and pull_request are supported."}`。

### 6.4 数据库直查

```powershell
# 安装 sqlitebrowser 或用 sqlite3 CLI
sqlite3 data/unity_check.db ".tables"
sqlite3 data/unity_check.db "SELECT id, event_type, status, overall_score, final_risk_level FROM github_events;"
sqlite3 data/unity_check.db "SELECT id, event_id, round_number, round_type, file_path, score FROM evaluation_rounds LIMIT 10;"
```

### 6.5 API 端点直接调用验证

```powershell
# 事件分页列表
curl http://localhost:8000/api/events

# 事件详情（含评估数据）
curl "http://localhost:8000/api/events/1?include=assessment"

# 评估轮次
curl http://localhost:8000/api/events/1/evaluations

# 综合评估
curl http://localhost:8000/api/events/1/assessment

# 看板数据
curl "http://localhost:8000/api/dashboard?section=summary"
curl "http://localhost:8000/api/dashboard?section=trends"
curl "http://localhost:8000/api/dashboard?section=distribution"
curl "http://localhost:8000/api/dashboard?section=scores"
curl "http://localhost:8000/api/dashboard?section=hotspots"
```

---

## 7. 常见问题排查

### 7.1 后端启动失败

| 问题 | 可能原因 | 解决 |
|------|---------|------|
| `uv` 命令未找到 | uv 未安装 | `pip install uv` 或参考 [uv 官方文档](https://docs.astral.sh/uv/) |
| `ImportError` | 依赖未安装 | 运行 `uv sync` |
| 端口被占用 | 8000 已被其他进程占用 | 修改 `.env` 中的 `APP_PORT`，或关闭占用进程 |
| `sqlite3` 相关错误 | 数据目录不存在 | 自动创建，或手动 `mkdir -p data` |

### 7.2 前端启动失败

| 问题 | 可能原因 | 解决 |
|------|---------|------|
| `npm install` 失败 | 网络问题 | 设置 npm 镜像 `npm config set registry https://registry.npmmirror.com` |
| 页面白屏/空白 | 依赖未正确安装 | `rm -rf node_modules && npm install` |
| API 请求 404 | 后端未启动或代理配置错误 | 检查后端是否在 8000 端口运行 |

### 7.3 Webhook 执行失败

| 问题 | 可能原因 | 解决 |
|------|---------|------|
| Git clone 失败 | 网络问题或 URL 不可达 | 检查 `clone_url` 是否正确；使用公开仓库或配置 SSH |
| 返回 `event_id` 但状态 failed | 评估流水线异常 | 查看后端日志获取详细错误信息 |
| LLM 评估超时 | DeepSeek API 响应慢 | 检查网络连接；降低 diff 文件数量 |
| `LLM_API_KEY` 无效 | Key 已过期或格式错误 | 在 [DeepSeek 平台](https://platform.deepseek.com) 确认 Key 状态 |
| 日志显示 "no diff content" | before/after SHA 相同 | 确保两次提交不同 |

### 7.4 前端不显示数据

| 问题 | 可能原因 | 解决 |
|------|---------|------|
| 看板为空 | 尚未触发任何 webhook | 先执行阶段三的模拟 webhook 请求 |
| 事件详情 missing | 事件 ID 不正确 | 在事件列表页确认正确 ID |
| 图表不渲染 | ECharts 加载失败 | 浏览器控制台查看报错，确认网络正常 |
| 前端 API 报错 | 后端未运行或地址不对 | 确认 `http://localhost:8000/health` 能正常返回 |

### 7.5 数据库问题

```powershell
# 重置数据库（删除后重启后端会自动重建）
rm data/unity_check.db
```

---

## 8. API 速查表

| 方法 | 路径 | 说明 | 关键参数 |
|------|------|------|---------|
| GET | `/health` | 健康检查 | — |
| POST | `/webhook/github` | 接收 Webhook | Header: `X-GitHub-Event`, `X-GitHub-Delivery` |
| GET | `/api/events` | 事件分页列表 | `page`, `page_size`, `event_type`, `risk_level`, `status` |
| GET | `/api/events/{id}` | 事件详情 | `include=assessment` |
| GET | `/api/events/{id}/evaluations` | 评估轮次列表 | — |
| GET | `/api/events/{id}/assessment` | 综合评估 | — |
| POST | `/api/events/{id}/re-evaluate` | 重新评估 | — |
| GET | `/api/dashboard` | 看板数据 | `section=summary\|trends\|distribution\|scores\|hotspots` |

---

## 全流程验证清单

- [ ] 环境依赖安装完成（Python 3.13, uv, Node.js 18+）
- [ ] `.env` 已配置 `LLM_API_KEY`
- [ ] `uv sync` 和 `npm install` 成功
- [ ] `uv run pytest tests/ -q` 全部通过
- [ ] 后端启动成功，`/health` 返回 ok
- [ ] 前端启动成功，页面可访问
- [ ] 模拟 push webhook 触发评估成功
- [ ] Webhook 返回 `status=success` 和 `event_id`
- [ ] 看板页面显示事件数据
- [ ] 事件列表可看到新事件
- [ ] 事件详情 Tab 页内容正确
- [ ] 统计中心有数据展示
- [ ] 幂等性（重复请求返回 accepted）验证通过
- [ ] 重评估功能正常
