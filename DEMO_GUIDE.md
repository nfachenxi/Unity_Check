# DEMO_GUIDE.md — 演示指南

## 环境要求

- Python 3.13+ (含 `uv`)
- Node.js 18+

## 启动步骤

### 1. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少配置 `LLM_API_KEY`。

### 2. 启动后端

```bash
uv sync
uv run uvicorn unity_check.main:app --app-dir src --reload
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

打开浏览器访问 `http://localhost:5173`。

## 演示流程

### 模拟 webhook 触发评估

```bash
# 使用 Demo 仓库的两次提交模拟 push
cd Demo/Unity_Check_Demo
INIT_SHA=$(git log --oneline | tail -1 | awk '{print $1}')
SCRIPT_SHA=$(git log --oneline | head -1 | awk '{print $1}')

curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d "{
    \"ref\": \"refs/heads/master\",
    \"before\": \"$INIT_SHA\",
    \"after\": \"$SCRIPT_SHA\",
    \"repository\": {
      \"full_name\": \"nfachenxi/Unity_Check_Demo\",
      \"ssh_url\": \"git@github.com:nfachenxi/Unity_Check_Demo.git\"
    },
    \"commits\": [{}]
  }"
```

等待 30-60 秒完成 LLM 评估。

### 查看结果

1. **事件列表** `/events` — 查看新事件及其评分
2. **事件详情** `/events/{id}` — 查看双维度评估结果和 AI 分析

## 快速验证清单

- [ ] `curl http://localhost:8000/health` 返回 ok
- [ ] `http://localhost:5173` 前端加载正常
- [ ] `LLM_API_KEY` 已配置且有效
