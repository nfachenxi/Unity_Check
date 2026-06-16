# Unity Check 使用指南

基于大模型与 Git 协作的 Unity 工程代码实时检测、多轮评估与智能通知系统。

---

## 一、系统概述

Unity Check 通过 GitHub Webhook 实时监听 Unity 工程代码推送，自动完成以下流程：

```
仓库注册 → Webhook 监听 → Git 拉取 → Diff 提取 → LLM 双维度评估 → 前端展示
```

每次代码推送都会自动触发评估，无需人工介入。

---

## 二、快速开始

### 2.1 环境要求

- Python 3.13+
- Node.js 18+
- uv（Python 包管理器）
- Git

### 2.2 安装

```bash
# 克隆项目
git clone <项目地址>
cd F:/work/Project

# 安装后端依赖
uv sync

# 安装前端依赖
cd frontend
npm install
cd ..

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY
```

### 2.3 启动

```bash
# 启动后端 (FastAPI)
uv run uvicorn unity_check.main:app --reload --port 8000

# 启动前端 (Vue 3) — 另开一个终端
cd frontend
npm run dev
```

- 后端地址：http://localhost:8000
- 前端地址：http://localhost:5173
- API 文档：http://localhost:8000/docs

---

## 三、仓库管理

### 3.1 注册仓库

**方式一：前端页面**

1. 打开前端 → 左侧导航点击「仓库管理」
2. 点击「添加仓库」按钮
3. 填写以下信息：

| 字段 | 必填 | 说明 |
|------|------|------|
| 仓库名称 | **是** | 格式 `owner/repo`，例如 `myteam/unity-project` |
| Clone URL | 否 | Git 克隆地址，例如 `https://github.com/myteam/unity-project.git` |
| Webhook Secret | 否 | GitHub Webhook 签名密钥，用于验证请求合法性 |
| SSH Key 路径 | 否 | 私有仓库 SSH 密钥路径，例如 `/root/.ssh/id_ed25519` |
| 分支过滤 | 否 | JSON 数组，例如 `["main", "release/*"]`，留空监听所有分支 |
| 活跃 | 否 | 是否启用此仓库的监听 |

4. 点击「保存」

**方式二：API**

```bash
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myteam/unity-project",
    "clone_url": "https://github.com/myteam/unity-project.git",
    "webhook_secret": "your-secret-here",
    "ssh_key_path": "/root/.ssh/id_ed25519",
    "branch_filter": "[\"main\", \"release/*\"]",
    "is_active": true
  }'
```

### 3.2 查看仓库列表

```
GET /api/repositories
```

或在前端「仓库管理」页面查看，表格包含：
- 仓库名称
- 状态（active / error / disabled）
- 是否活跃
- 最后同步时间
- 错误信息（同步失败时显示）
- 操作按钮（编辑 / 删除）

### 3.3 更新仓库配置

```
PUT /api/repositories/{id}
```

可更新字段：`clone_url`、`webhook_secret`、`ssh_key_path`、`branch_filter`、`is_active`、`status`

### 3.4 删除仓库

```
DELETE /api/repositories/{id}
```

删除仓库**不会**删除已关联的事件记录，仅将事件的 `repository_id` 置为 NULL。

---

## 四、GitHub Webhook 配置

### 4.1 在 GitHub 上配置 Webhook

1. 打开 GitHub 仓库 → **Settings** → **Webhooks** → **Add webhook**
2. 填写：

| 字段 | 值 |
|------|------|
| Payload URL | `http://你的服务器地址:8000/webhook/github` |
| Content type | `application/json` |
| Secret | 与仓库注册时填写的 `webhook_secret` **保持一致** |
| Events | 勾选 **Pushes** 和 **Pull requests** |
| Active | ✅ 勾选 |

3. 点击 **Add webhook**

### 4.2 验证配置

配置完成后，GitHub 会自动发送一个 `ping` 事件进行验证。可以在系统事件列表中确认是否收到。

---

## 五、完整处理流程

### 5.1 Webhook 处理流程

```
GitHub 推送代码
     ↓
发送 Webhook POST /webhook/github
     ↓
① 事件类型检查 → 仅支持 push / pull_request
     ↓
② 幂等性检查 → 重复 delivery_id 跳过
     ↓
③ 仓库查找 ← 在 repositories 表中搜索
     ├─ 未注册 → 返回 404（请先注册仓库）
     └─ 已禁用 → 返回 403
     ↓
④ 签名校验（如配置了 webhook_secret）
     ├─ 无签名头 → 返回 401
     ├─ 签名不匹配 → 返回 401
     └─ 验证通过 → 继续
     ↓
⑤ 分支过滤（如配置了 branch_filter）
     ├─ 分支不匹配 → 跳过（返回 skipped）
     └─ 分支匹配 → 继续
     ↓
⑥ Git 操作
     ├─ 使用 SSH Key（如配置）
     ├─ 克隆/拉取 bare 仓库
     └─ 提取 diff
     ↓
⑦ LLM 评估流水线
     ├─ 提取 .cs 文件
     ├─ 维度 A：功能/最佳实践 评估
     ├─ 维度 B：安全/性能/健康度 评估
     └─ 聚合评分
     ↓
⑧ 仓库状态更新
     ├─ 成功 → status=active, last_synced_at=now
     └─ 失败 → status=error, error_message=...
     ↓
⑨ 返回结果
```

### 5.2 分支过滤规则

`branch_filter` 使用 JSON 数组存储分支名模式，支持 `*` 通配符：

| 配置 | 效果 |
|------|------|
| `null` 或空 | 监听所有分支 |
| `["main"]` | 仅监听 main 分支 |
| `["main", "develop"]` | 监听 main 和 develop 分支 |
| `["release/*"]` | 监听所有 release/ 开头的分支 |
| `["main", "release/*", "hotfix/*"]` | 多模式组合 |

### 5.3 SSH Key 配置

**全局配置**（对所有仓库生效）：
```bash
# 在 .env 中添加
GIT_SSH_KEY_PATH=/root/.ssh/id_ed25519
```

**仓库级配置**（覆盖全局，仅对该仓库生效）：
在注册或编辑仓库时填写 `ssh_key_path` 字段。

> 注意：系统会自动添加 `-o StrictHostKeyChecking=no` 参数，避免首次连接时需要交互确认。

---

## 六、前端使用

### 6.1 页面导航

| 页面 | 路径 | 功能 |
|------|------|------|
| 概览看板 | `/` | 总览统计：事件数、风险分布、评分趋势、最近事件 |
| 事件列表 | `/events` | 查看所有处理过的事件，支持按类型/风险/状态/仓库筛选 |
| 仓库管理 | `/repositories` | 管理注册的仓库：添加/编辑/删除 |
| 统计中心 | `/stats` | 评分分布、热点文件分析 |

### 6.2 查看评估结果

1. 进入「事件列表」
2. 点击任意事件行进入详情
3. 详情页包含：
   - 事件基本信息（仓库、SHA、状态）
   - 综合评分与风险等级
   - 双维度评分（功能/最佳实践 + 安全/性能/健康度）
   - 各文件的逐项评估结果
   - 执行摘要与建议

### 6.3 重新评估

在事件详情页可以对已处理的事件发起「重新评估」，系统会重新执行 LLM 评估流水线并更新结果。

---

## 七、安全说明

### 7.1 Webhook 签名验证

- 为每个仓库分配独立的 `webhook_secret`
- 系统使用 HMAC-SHA256 验证 `X-Hub-Signature-256` 头
- 未注册仓库的 Webhook 请求被直接拒绝（404）
- 签名验证失败的请求被拒绝（401）

### 7.2 仓库访问控制

- 可禁用仓库（`is_active=false`），禁用的仓库不处理任何 Webhook
- SSH Key 可独立配置，不同仓库可以使用不同的认证凭据

### 7.3 API 安全

- `GET /api/repositories` 返回中**不包含** `webhook_secret` 字段
- 编辑仓库时需要重新输入 Secret 才能修改

---

## 八、配置文件参考

### .env

```ini
APP_NAME=Unity Check
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000
APP_LOG_LEVEL=INFO

DATABASE_URL=sqlite:///./data/unity_check.db

LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-your-key-here

GIT_CLONE_BASE_DIR=./repos
GIT_SSH_KEY_PATH=                      # 可选：全局 SSH Key
```

---

## 九、常见问题

### Q: Webhook 返回 404，提示仓库未注册

A: 该仓库尚未在系统中注册。请先通过前端「仓库管理」或 API 注册该仓库。

### Q: Webhook 返回 401，签名不匹配

A: GitHub Webhook 中配置的 Secret 与系统仓库配置的 `webhook_secret` 不一致。请检查两端 Secret 是否相同。

### Q: 克隆私有仓库失败

A: 如果使用 SSH，请确保：
1. SSH Key 已添加到 GitHub 账户的 Deploy Keys 或 SSH Keys
2. SSH Key 路径在仓库配置或 `GIT_SSH_KEY_PATH` 中正确指定
3. SSH Key 有正确的文件权限（`chmod 600`）

如果使用 HTTPS，确保仓库是公开的，或使用 `https://token@github.com/owner/repo.git` 格式。

### Q: 如何只监听 main 分支？

A: 在仓库配置中将 `branch_filter` 设为 `["main"]`。

### Q: 删除仓库会影响历史数据吗？

A: 不会。删除仓库仅移除仓库注册信息，已处理的事件记录完整保留。
