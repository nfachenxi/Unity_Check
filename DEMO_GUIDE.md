# DEMO_GUIDE.md — 答辩演示指南

## 环境要求

- Docker Desktop 运行中
- Python 3.11+ (含 `uv`)
- Node.js 18+
- 约 4GB 可用磁盘空间（Docker 镜像 + 数据库）

## 答辩前准备（提前 30 分钟）

### 1. 配置环境变量

```bash
# 从项目根目录
cp .env.example .env
```

编辑 `.env`，至少配置：

```env
LLM_API_KEY=sk-your-deepseek-key    # 必须！否则 LLM 评估跳过
GITHUB_REMOTE_REPO=git@github.com:nfachenxi/Unity_Check_Demo.git
DATABASE_URL=postgresql+psycopg://unitycheck:unitycheck@db:5432/unitycheck
REDIS_URL=redis://redis:6379/0
```

### 2. 验证 Demo 仓库可用

```bash
# 确认 Demo 仓库有初始提交和 C# 脚本提交
cd Demo/Unity_Check_Demo
git log --oneline
# 预期输出:
# 860af7e 添加演示用C#脚本
# 03a00ae 初始化
cd ../..
```

记录两个 SHA 供后续使用：

```bash
INIT_SHA=$(cd Demo/Unity_Check_Demo && git log --oneline | tail -1 | awk '{print $1}')
SCRIPT_SHA=$(cd Demo/Unity_Check_Demo && git log --oneline | head -1 | awk '{print $1}')
echo "INIT: $INIT_SHA  SCRIPTS: $SCRIPT_SHA"
```

### 3. 启动全部 Docker 服务

```bash
docker compose up -d
```

等待所有服务健康就绪（约 30 秒）：

```bash
docker compose ps
# 预期 5 个服务: api, worker, db, redis, roslyn 全部 healthy/running
```

验证健康检查：

```bash
curl http://localhost:8000/health
# 预期: {"status":"ok"}
```

### 4. 生成种子历史数据

```bash
# 生成 50 条历史事件，跨度 21 天，使看板趋势图有数据
uv run python scripts/seed_demo_history.py --count 50 --days 21 --clean
```

### 5. 启动前端

```bash
cd frontend
npm run dev &
cd ..
```

打开浏览器访问 `http://localhost:5173`，确认前端正常加载。

---

## 答辩演示流程 (12 分钟)

### 环节 1：引入 (2 分钟)

**口播要点**:
- Unity 项目开发中代码质量问题突出（性能、安全、命名规范）
- 传统 Code Review 依赖人工，耗时且不全面
- 本系统：自动化代码质量检测与评估体系

**屏幕展示**: 前端概览看板 `/` — 展示已有的种子历史数据
- 趋势折线图：2 周内提交量和评分的上升趋势
- 风险分布饼图：low/medium/high/critical 比例
- 问题类型柱状图：各类问题的分布

### 环节 2：现场演示 (5 分钟)

#### Step 1: 模拟 push webhook

```bash
# 使用 Demo 仓库的两次提交模拟 push
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

**预期输出**: `{"status":"accepted","event_id":"...","task_id":"..."}`

记录返回的 `event_id`。

#### Step 2: 注入规则数据

```bash
# 替换 <EVENT_ID> 为上一步返回的 event_id
uv run python scripts/seed_demo_data.py <EVENT_ID>
```

**预期输出**: `已为 event_id=<ID> 写入 15 条预设 rule_results`

#### Step 3: 触发三轮评估

```bash
curl -X POST http://localhost:8000/events/<EVENT_ID>/re-evaluate
```

等待 30-60 秒（LLM 调用 DeepSeek 进行两轮语义评估）。

#### Step 4: 查看评估结果

**终端查看**:

```bash
# 查看综合评估
curl http://localhost:8000/events/<EVENT_ID>/assessment | python -m json.tool
```

**口播要点**:
- 三轮评估全部完成
- 每轮有独立记录（round_number 1/2/3）
- Token 用量和耗时全部记录

#### Step 5: 前端展示

刷新浏览器，依次展示：

1. **概览看板** `/` — 新事件已出现在最近事件表中
2. **事件列表** `/events` — 新事件可被筛选到
3. **事件详情** `/events/{id}`:
   - 事件信息 Tab：commit SHA、diff 大小、状态
   - 代码 Diff Tab：10 个新 C# 文件的完整 diff
   - 规则检测 Tab：15 条静态分析违规（按严重度着色）
   - LLM 语义评估 Tab：Round 2 发现的语义问题
   - LLM 综合评分 Tab：Round 3 的最终评分和风险等级
4. **统计中心** `/stats` — 展示评分趋势和历史数据

#### Step 6: 查看通知

```bash
curl http://localhost:8000/api/notifications?event_id=<EVENT_ID> | python -m json.tool
```

展示企微 Markdown 和飞书卡片 JSON 消息内容。

### 环节 3：架构讲解 (2 分钟)

**展示**: `ARCHITECTURE.md` 的架构图（或 PPT 版本）

**口播要点**:
- 总架构：webhook → Celery → 三轮评估 → 通知 + 看板
- 关键设计决策：
  - Roslyn 容器化（解耦技术栈）
  - 三轮评估独立持久化（可调试、高容错）
  - 通知与发送分离（不耦合第三方 API）
  - webhook 幂等设计（防止重复评估）
- 数据模型：4 张核心表及其关系

### 环节 4：关键结果 (2 分钟)

**屏幕展示**: 前端看板数据（如果种子数据包含了趋势变化）

**口播要点**:
- 系统可在 60 秒内完成从 webhook 到最终评估的全流程
- 三轮评估架构保证了检测深度（规则 + 语义 + 综合）
- 看板提供了代码质量的全局视图
- 通知系统支持企业微信和飞书双通道

**数据亮点** (如果使用种子数据):
- 2-3 周内 50 次评估的趋势可视化
- 质量改善趋势（后期风险逐步降低）

### 环节 5：局限与未来 (1 分钟)

**口播要点**:
- **已知限制**:
  - Roslyn 分析器包运行时加载受限，当前采用种子数据方案
  - 仅支持 Unity C# 代码（`.cs` 文件）
  - LLM 评估受 API 响应时间影响（单次约 10-30 秒）
- **未来扩展方向**:
  - 支持 Unity Shader / Prefab 变更分析
  - 集成更多分析器（SonarQube、Unity Test Framework）
  - 对接真实的企微/飞书机器人

---

## 故障预案

| 问题 | 解决方案 |
|------|---------|
| Docker 启动失败 | 检查 Docker Desktop 运行状态，重启 Docker |
| LLM API 超时 | LLM 有 3 次重试机制；如全部失败，R3 仍会执行并标注缺失数据 |
| 前端白屏 | 检查 Vite 代理是否正确，后端 `http://localhost:8000` 是否可访问 |
| 种子数据脚本报错 | 先 `--clean` 再重新生成 |
| webhook 触发后无响应 | 检查 Celery Worker 日志 `docker compose logs worker` |
| 数据库表不存在 | 重启 API 容器（FastAPI lifespan 自动建表） |

## 快速验证清单

答辩前逐项确认：

- [ ] `docker compose ps` 全部 healthy
- [ ] `curl http://localhost:8000/health` 返回 ok
- [ ] `curl http://localhost:8080/health` 返回 ok (Roslyn)
- [ ] `http://localhost:5173` 前端加载正常，4 个页面可切换
- [ ] 种子数据已生成，看板有图表数据
- [ ] `LLM_API_KEY` 已配置且有效
- [ ] Demo 仓库两个 SHA 已记录
