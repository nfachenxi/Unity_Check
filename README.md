# Unity Check

基于大模型与 Git 协作的 Unity 工程代码实时检测与质量评估系统。

通过 GitHub Webhook 实时监听 Unity C# 代码推送，利用 DeepSeek LLM 对每个 `.cs` 文件进行双维度自动化评估（功能/最佳实践 + 安全/性能/健康度），在前端看板呈现评分、风险分析与改进建议。

---

## 目录

- [核心功能](#核心功能)
- [技术栈](#技术栈)
- [系统架构](#系统架构)
- [快速本地开发](#快速本地开发)
- [Debian 服务器手动部署](#debian-服务器手动部署)
- [使用指南](#使用指南)
- [API 参考](#api-参考)
- [测试](#测试)
- [常见问题](#常见问题)

---

## 核心功能

| 功能 | 说明 |
|------|------|
| **Webhook 实时接收** | 监听 GitHub push / pull_request 事件，自动触发评估 |
| **Git 差异分析** | 自动 clone/fetch 仓库，提取代码 diff |
| **LLM 双维度评估** | 每个 `.cs` 文件经过功能/最佳实践 + 安全/性能/健康度两轮评估 |
| **程序化评分聚合** | 综合双维度评分生成总体分数、风险等级与建议 |
| **可视化看板** | Vue3 + Element Plus 前端提供概览、详情、统计与仓库管理 |
| **仓库管理** | 支持多仓库注册、独立 Webhook Secret、分支过滤、SSH Key 配置 |
| **安全验证** | HMAC-SHA256 签名校验、注册仓库白名单、幂等防重复处理 |

## 技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| Web 框架 | FastAPI |
| 数据库 | SQLite (SQLAlchemy 2.0 ORM) |
| 配置管理 | Pydantic Settings |
| LLM | DeepSeek (OpenAI 兼容 SDK) |
| Git 操作 | GitPython |
| 包管理 | uv |
| 前端框架 | Vue 3 + Vite |
| UI 组件 | Element Plus |
| 图表 | ECharts (vue-echarts) |
| 测试 | pytest + pytest-cov |

## 系统架构

```
GitHub Push/PR
     │
     ▼
[FastAPI Webhook]
     │
     ├── ① 事件类型检查 (push / pull_request)
     ├── ② 幂等检查 (delivery_id)
     ├── ③ 仓库查找 (已注册仓库白名单)
     ├── ④ 签名校验 (HMAC-SHA256)
     ├── ⑤ 分支过滤 (fnmatch 白名单)
     │
     ▼
[同步处理流程]
     │
     ├── ⑥ Git clone/fetch → diff 提取
     │
     ├── ⑦ 提取 .cs 文件
     │
     ├── ⑧ 双维度 LLM 评估 (每文件 × 2)
     │   ├── 维度A: 功能与最佳实践 (Unity API/MonoBehaviour/GameObject 等)
     │   └── 维度B: 安全、性能与健康度 (GC/注入/SOLID 等)
     │
     ├── ⑨ 程序化聚合评分
     │   ├── overall_score = 平均分
     │   ├── risk_level = 最高严重度
     │   └── recommendation = blocked / needs_review / merge_ready
     │
     └── ⑩ 结果入库
           │
           ▼
     [SQLite 数据库]
           │
           ▼
     [Vue3 前端看板]
       ├── /             概览看板
       ├── /events       事件列表
       ├── /events/:id   事件详情
       ├── /repositories 仓库管理
       └── /stats        统计中心
```

### 数据模型

```
github_events (主表)
├── delivery_id (幂等键)
├── event_type (push / pull_request)
├── status (queued → success / failed)
├── overall_score · final_risk_level · recommendation
├── dimension_a_score · dimension_b_score
├── executive_summary
│
├── 1:N ── evaluation_rounds (每文件 × 每维度)
│           ├── file_path
│           ├── round_type (functionality_best_practices / security_performance_health)
│           ├── score · output_data (JSON: findings[])
│           └── model_name · tokens_used · duration_ms
│
└── N:1 ── repositories (仓库注册)
            ├── clone_url · webhook_secret · ssh_key_path
            ├── branch_filter (JSON 数组)
            └── is_active · status
```

## 快速本地开发

### 前置依赖

- Python 3.13+
- Node.js 18+
- uv（Python 包管理器）
- Git

### 1. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY
```

### 2. 安装依赖并启动后端

```bash
uv sync
uv run uvicorn unity_check.main:app --app-dir src --reload
```

后端启动在 `http://localhost:8000`。API 文档：`http://localhost:8000/docs`

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端启动在 `http://localhost:5173`，API 自动代理到后端 8000 端口。

### 4. 验证

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Debian 服务器手动部署

以下步骤适用于 Debian 12 / Ubuntu 22.04+ 服务器，手动部署 Unity Check 并通过 systemd 实现开机自启。

### 1. 服务器基础准备

```bash
# 更新系统
sudo apt update && sudo apt upgrade -y

# 安装必要工具
sudo apt install -y git curl wget build-essential openssh-client ca-certificates
```

### 2. 安装 uv（Python 包管理器）

```bash
curl -fsSL https://astral.sh/uv/install.sh | sh

# 将 uv 添加到 PATH（重新登录或手动执行）
source ~/.bashrc
# 或手动添加：export PATH="$HOME/.local/bin:$PATH"

# 验证
uv --version
```

> 如果服务器无法直连 GitHub，使用镜像源：
> ```bash
> curl -fsSL https://ghproxy.net/https://raw.githubusercontent.com/astral-sh/uv/main/install.sh | sh
> ```

### 3. 安装 Node.js

```bash
# 方法一：使用 NodeSource 官方源（推荐）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs

# 方法二：国内镜像（清华大学）
sudo apt install -y xz-utils
wget https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/v20.18.3/node-v20.18.3-linux-x64.tar.xz
sudo tar -xJf node-v20.18.3-linux-x64.tar.xz -C /usr/local --strip-components=1

# 验证
node --version  # 应输出 v20.x
npm --version   # 应输出 10.x
```

### 4. 克隆项目并配置

```bash
# 创建部署目录
sudo mkdir -p /opt/unity_check
sudo chown $USER:$USER /opt/unity_check

# 克隆项目（替换为实际仓库地址）
git clone <你的仓库地址> /opt/unity_check

# 或使用本地文件传输后复制：
# rsync -a --exclude='.git/' --exclude='.venv/' ./ /opt/unity_check/

# 进入部署目录
cd /opt/unity_check

# 创建运行时目录
mkdir -p data repos logs
```

### 5. 配置环境变量

```bash
# 从模板复制配置
cp .env.example .env

# 编辑 .env
nano .env
```

关键配置项：

```ini
APP_NAME=Unity Check
APP_ENV=production          # 生产模式：启用前端静态文件服务
APP_HOST=0.0.0.0
APP_PORT=8000               # 可改为其他端口（如 8080、80 需 root）
APP_LOG_LEVEL=INFO

DATABASE_URL=sqlite:///./data/unity_check.db

LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-你的密钥       # ⚠️ 必须填入有效的 DeepSeek API Key

GIT_CLONE_BASE_DIR=./repos
FRONTEND_DIST_DIR=./frontend/dist
```

> **修改端口**：直接修改 `APP_PORT` 为其他值（如 `8080`）。如果使用 `80` 或 `443`（需 root 权限），建议通过 Nginx 反向代理而非直接监听。

### 6. 安装 Python 依赖并构建前端

```bash
cd /opt/unity_check

# 安装 Python 依赖
uv sync --python 3.13

# 安装前端依赖
cd frontend
npm ci --no-audit --no-fund
npm run build
cd ..
```

构建完成后，前端静态文件位于 `frontend/dist/`。

### 7. 验证服务

```bash
# 手动启动测试
cd /opt/unity_check
uv run uvicorn unity_check.main:app --app-dir src --host 0.0.0.0 --port 8000

# 新终端中测试 API
curl http://localhost:8000/health

# 测试前端（应返回 HTML）
curl http://localhost:8000/ | head -5

# 测试通过后，按 Ctrl+C 停止测试进程
```

### 8. 配置 systemd 实现持久化运行

#### 8.1 创建专用系统用户（可选但推荐）

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin unity-check
sudo chown -R unity-check:unity-check /opt/unity_check
```

#### 8.2 创建 systemd 服务文件

```bash
sudo nano /etc/systemd/system/unity-check.service
```

内容如下：

```ini
[Unit]
Description=Unity Check — LLM-powered Unity code review service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=unity-check
Group=unity-check
WorkingDirectory=/opt/unity_check
ExecStart=/opt/unity_check/.venv/bin/uv run uvicorn unity_check.main:app --app-dir src --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10
StandardOutput=append:/opt/unity_check/logs/unity-check.log
StandardError=append:/opt/unity_check/logs/unity-check-error.log

# 安全加固
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/unity_check /tmp
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

> 如果修改了端口，同步修改 `ExecStart` 中的 `--port` 值。

#### 8.3 启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启用开机自启
sudo systemctl enable unity-check

# 启动服务
sudo systemctl start unity-check

# 查看状态
sudo systemctl status unity-check

# 查看实时日志
sudo journalctl -u unity-check -f
```

**服务管理命令速查：**

| 操作 | 命令 |
|------|------|
| 启动 | `sudo systemctl start unity-check` |
| 停止 | `sudo systemctl stop unity-check` |
| 重启 | `sudo systemctl restart unity-check` |
| 查看状态 | `sudo systemctl status unity-check` |
| 实时日志 | `sudo journalctl -u unity-check -f` |
| 查看最近日志 | `sudo journalctl -u unity-check -n 50 --no-pager` |

#### 8.4 修改配置后的重启流程

```bash
# 修改配置（如端口、API Key 等）
sudo nano /opt/unity_check/.env

# 重启服务使配置生效
sudo systemctl restart unity-check

# 验证重启成功
sudo systemctl status unity-check
curl http://localhost:8000/health
```

### 9. 配置 GitHub 访问（SSH 密钥）

如果仓库是私有的，Unity Check 需要 SSH 密钥才能 clone/fetch。

#### 9.1 生成 SSH 密钥

```bash
# 切换到服务用户
sudo -u unity-check bash

# 生成密钥（无密码短语）
ssh-keygen -t ed25519 -C "unity-check@$(hostname)" -f ~/.ssh/id_ed25519 -N ""

# 添加密钥到 ssh-agent
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# 查看公钥
cat ~/.ssh/id_ed25519.pub

# 退出服务用户
exit
```

#### 9.2 添加公钥到 GitHub

1. 打开 GitHub → **Settings** → **SSH and GPG keys** → **New SSH key**
2. 标题：`Unity Check Server`
3. Key：粘贴上一步输出的公钥内容
4. 点击 **Add SSH key**

#### 9.3 验证连接

```bash
sudo -u unity-check ssh -T git@github.com
# 预期输出: Hi <username>! You've successfully authenticated...
```

#### 9.4 配置 SSH 免交互确认

```bash
sudo -u unity-check bash -c 'cat >> ~/.ssh/config << EOF
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking accept-new
EOF'
```

### 10. 配置 GitHub Webhook

#### 10.1 在 GitHub 上添加 Webhook

1. 打开目标 GitHub 仓库 → **Settings** → **Webhooks** → **Add webhook**
2. 填写：

| 字段 | 值 |
|------|------|
| Payload URL | `http://你的服务器IP:8000/webhook/github` |
| Content type | `application/json` |
| Secret | 一个随机字符串（与后续仓库配置一致） |
| SSL verification | Disable（或配置 HTTPS） |
| Events | 勾选 **Pushes** 和 **Pull requests** |
| Active | ✅ 勾选 |

3. 点击 **Add webhook**

#### 10.2 在 Unity Check 中注册仓库

通过 API 注册：

```bash
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{
    "name": "你的GitHub用户名/仓库名",
    "clone_url": "git@github.com:你的GitHub用户名/仓库名.git",
    "webhook_secret": "与Webhook配置相同的Secret",
    "ssh_key_path": "/home/unity-check/.ssh/id_ed25519",
    "branch_filter": "[\"main\", \"release/*\"]",
    "is_active": true
  }'
```

或在浏览器打开前端页面 → 仓库管理 → 添加仓库，填写相同信息。

#### 10.3 验证 Webhook

GitHub 配置成功后会自动发送 ping 事件。在 Unity Check 事件列表页面查看是否收到 ping。

---

## 使用指南

### 仓库管理

**注册仓库**（必须）：系统只处理已注册仓库的 Webhook 请求。可通过前端"仓库管理"页面或 API 注册。

**配置项说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| 仓库名称 | **是** | 格式 `owner/repo`，如 `myteam/unity-project` |
| Clone URL | 否 | Git 克隆地址，默认使用 `https://github.com/owner/repo.git` |
| Webhook Secret | 否 | GitHub Webhook 签名密钥，用于 HMAC-SHA256 验证 |
| SSH Key 路径 | 否 | 私有仓库 SSH 密钥路径 |
| 分支过滤 | 否 | JSON 数组，如 `["main", "release/*"]`，留空监听所有分支 |
| 活跃 | 否 | 是否启用此仓库的监听 |

**分支过滤规则**：

| 配置 | 效果 |
|------|------|
| 空 | 监听所有分支 |
| `["main"]` | 仅监听 main 分支 |
| `["main", "release/*"]` | 监听 main 和所有 release/ 开头的分支 |

### 前端页面

| 页面 | 路径 | 功能 |
|------|------|------|
| 概览看板 | `/` | 事件总数、平均评分、风险分布图、评分趋势、最近事件列表 |
| 事件列表 | `/events` | 分页查看所有事件，支持按类型/风险/状态/仓库筛选 |
| 事件详情 | `/events/:id` | 事件基本信息、综合评分仪表盘、双维度评估结果、文件级 findings |
| 仓库管理 | `/repositories` | 仓库注册、编辑、删除、状态查看 |
| 统计中心 | `/stats` | 评分趋势、文件热点 TOP10 |

### 评估结果解读

每次 Webhook 处理完成后，系统生成以下评估指标：

- **综合评分 (overall_score)**：0-100，所有维度分数的平均
- **风险等级 (risk_level)**：low / medium / high / critical / unknown
- **建议 (recommendation)**：
  - `merge_ready`：评分 ≥ 80 且无高风险问题
  - `needs_review`：存在中等风险问题
  - `blocked`：评分 < 50 或存在 critical 问题
- **执行摘要 (executive_summary)**：中文总结本次评估的关键发现

### 重新评估

在事件详情页面可对已处理的事件触发"重新评估"，系统会重新执行完整评估流水线并更新结果。

---

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/webhook/github` | GitHub webhook 接收入口 |
| GET | `/api/events` | 事件分页列表（支持 event_type/risk_level/status/repository 筛选） |
| GET | `/api/events/{id}` | 事件详情（支持 `?include=assessment`） |
| GET | `/api/events/{id}/evaluations` | 评估轮次列表 |
| GET | `/api/events/{id}/assessment` | 综合评估结果 |
| POST | `/api/events/{id}/re-evaluate` | 重新触发评估 |
| GET | `/api/dashboard?section=` | 看板数据（section: summary/trends/distribution/scores/hotspots） |
| GET | `/api/repositories` | 仓库列表（不含 webhook_secret） |
| POST | `/api/repositories` | 创建仓库 |
| PUT | `/api/repositories/{id}` | 更新仓库配置 |
| DELETE | `/api/repositories/{id}` | 删除仓库（关联事件保留） |

---

## 测试

### 运行单元测试

```bash
# 快速运行全部测试（默认 Mock LLM，无需 API Key）
uv run pytest tests/ -q

# 详细输出
uv run pytest tests/ -v

# 覆盖率报告
uv run pytest tests/ --cov=src/unity_check --cov-report=term

# 单模块测试
uv run pytest tests/test_webhook.py -v
uv run pytest tests/test_orchestrator.py -v
uv run pytest tests/test_llm_v2.py -v
```

### 端到端验证

```bash
# 1. 确保后端已启动
curl http://localhost:8000/health

# 2. 注册测试仓库
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"name": "demo/Unity_Check_Demo", "is_active": true}'

# 3. 模拟 push webhook
curl -X POST http://localhost:8000/webhook/github \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -H "X-GitHub-Delivery: test-001" \
  -d '{
    "ref": "refs/heads/main",
    "before": "0000000",
    "after": "HEAD",
    "repository": {
      "full_name": "demo/Unity_Check_Demo",
      "clone_url": "https://github.com/nfachenxi/Unity_Check_Demo.git"
    },
    "commits": [{}]
  }'

# 4. 查看事件
curl http://localhost:8000/api/events
```

---

## 常见问题

### Webhook 返回 404？
该仓库尚未在系统中注册。先通过前端或 API 注册仓库。

### Webhook 签名不匹配（401）？
GitHub Webhook 中配置的 Secret 与 Unity Check 仓库配置的 `webhook_secret` 不一致。

### 克隆私有仓库失败？
1. SSH Key 已添加到 GitHub？（Settings → SSH and GPG Keys）
2. SSH Key 路径在仓库配置或 `.env` 中正确指定？
3. SSH Key 权限正确？（`chmod 600`）
4. 连接测试：`ssh -T git@github.com`

### 修改配置后如何生效？
修改 `.env` 后执行 `sudo systemctl restart unity-check`。

### 如何重置数据库？
```bash
sudo systemctl stop unity-check
sudo rm /opt/unity_check/data/unity_check.db
sudo systemctl start unity-check
# 服务会自动重建数据库
```

### 如何查看服务日志？
```bash
sudo journalctl -u unity-check -f          # 实时日志
sudo journalctl -u unity-check -n 100       # 最近 100 行
sudo journalctl -u unity-check --since "5 min ago"  # 最近 5 分钟
```

---

## 安全说明

- **仓库注册白名单**：只有已注册仓库的 webhook 被处理，未注册返回 404
- **HMAC-SHA256 签名验证**：每个仓库独立配置 `webhook_secret`，签名不匹配返回 401
- **幂等性**：通过 `X-GitHub-Delivery` 唯一键防重复处理
- **Secret 保护**：API 返回仓库列表时自动过滤 `webhook_secret` 字段
- **SSH Key 隔离**：不同仓库可使用不同的 SSH Key

## 项目结构

```
├── src/unity_check/       # Python 后端核心
│   ├── main.py            # FastAPI 应用 + 全部 API 端点
│   ├── config.py          # 配置管理 (Pydantic Settings)
│   ├── db.py              # 数据库连接与迁移
│   ├── models.py          # ORM 数据模型 (SQLAlchemy)
│   ├── orchestrator.py    # 评估流水线编排
│   ├── llm.py             # LLM 集成 (DeepSeek, 重试)
│   ├── git_service.py     # Git clone/fetch/diff
│   ├── rule_service.py    # Diff 解析 (.cs 提取)
│   ├── repository_service.py  # 仓库 CRUD
│   └── webhook_service.py     # Webhook 签名验证
├── frontend/              # Vue3 + Element Plus 前端
│   ├── src/               # 源码 (5 个页面 + 组件)
│   └── dist/              # 构建产物
├── tests/                 # pytest 测试
├── data/                  # 运行时数据 (SQLite)
├── repos/                 # Git bare 仓库缓存
└── logs/                  # 运行日志
```
