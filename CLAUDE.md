# Unity Check — 项目开发规范

> 基于大模型与 Git 协作的 Unity 工程代码实时检测、多轮评估与智能通知系统

## 技术栈

Python 3.13 · FastAPI · Celery + Redis · PostgreSQL 16 · SQLAlchemy 2.0 · DeepSeek · Vue 3 + Element Plus · Roslyn .NET 8 · Docker Compose · uv

## 项目结构

```
src/unity_check/       # Python 后端核心 (FastAPI + Celery)
roslyn-analyzer/       # Roslyn .NET 8 分析器 (Docker 容器)
frontend/              # Vue3 + Element Plus + ECharts 前端
tests/                 # pytest 测试
scripts/               # 种子数据/工具脚本
Demo/                  # 演示用 Unity C# 项目
Docs/                  # 规划设计文档 (只读参考)
```

## 核心约定

### 命名规范

| 类别 | 约定 | 示例 |
|------|------|------|
| Python 模块/文件 | `snake_case` | `git_service.py`, `rule_service.py` |
| 类 | `PascalCase` | `GithubEvent`, `RuleResult` |
| 公共函数/方法 | `snake_case` | `ensure_bare_repo()`, `run_evaluation_pipeline()` |
| 私有函数 | `_` 前缀 + `snake_case` | `_build_rule_results_summary()`, `_should_notify()` |
| 变量 | `snake_case` | `event_id`, `diff_content` |
| 常量/配置键 | `UPPER_SNAKE` | `DATABASE_URL`, `MAX_DIFF_CHARS` |
| 数据库列 | `snake_case` | `event_type`, `before_sha` |
| API 路径 | `kebab-case` | `/api/dashboard/summary`, `/events/{id}/rules` |
| Celery 任务名 | `unity_check.<action>_<target>` | `unity_check.process_github_event` |
| 测试文件 | `test_<module>.py` | `test_git_service.py` |
| 测试函数 | `test_<what>` | `test_extract_sha_from_payload` |
| ORM back_populates | 与关系属性名一致 | `back_populates="rule_results"` |

详见 [.claude/standards/naming.md](.claude/standards/naming.md)

### Git 提交规范

```
<type>(<scope>): <简短描述>
```

| type | 场景 |
|------|------|
| `feat` | 新功能 |
| `fix` | 问题修复 |
| `refactor` | 重构 (行为不变) |
| `test` | 测试 |
| `docs` | 文档/规范 |
| `chore` | 构建/工具/依赖 |

**scope**: `core`(编排/任务), `api`(FastAPI路由), `llm`(大模型), `git`(Git服务), `roslyn`(规则检测), `notify`(通知), `frontend`(前端), `config`(配置), `data`(种子数据)

**示例**: `feat(api): add dashboard summary endpoint`, `fix(llm): handle json parse retry on empty response`

详见 [.claude/standards/git.md](.claude/standards/git.md)

### 代码风格

- Python 3.13+, 包管理用 `uv`
- 所有文件顶部写 `from __future__ import annotations`
- 公共函数必须有类型注解和单行 docstring
- 日志用 `logger = logging.getLogger(__name__)`
- 配置通过 `get_settings()` 获取 (Pydantic Settings, `.env` 驱动)
- import 顺序: 标准库 → 第三方 → 项目内部 (空行分隔)

详见 [.claude/standards/code-style.md](.claude/standards/code-style.md)

### API 设计

- RESTful 风格，路径用 `kebab-case`
- 分页接口返回 `{items, page, page_size, total, total_pages}`
- 错误用 `HTTPException(status_code=N, detail="...")`
- Webhook 入口必须校验签名 + 幂等 (delivery_id)
- 异步操作用 `status_code=202` + Celery task_id 响应

详见 [.claude/standards/api-design.md](.claude/standards/api-design.md)

### 数据模型

- SQLAlchemy 2.0 DeclarativeBase，`Mapped[]` + `mapped_column()` 语法
- 所有表包含 `created_at` (server_default=func.now())
- 可变记录包含 `updated_at` (onupdate=func.now())
- 时间列统一 `DateTime(timezone=True)`
- 外键必须设 `ondelete="CASCADE"`
- 关系用 `back_populates` (双向), `cascade="all, delete-orphan"`
- 复合索引在 `__table_args__` 中定义

详见 [.claude/standards/data-model.md](.claude/standards/data-model.md)

### 异步任务 (Celery)

- 任务函数用 `@celery_app.task(name="unity_check.<name>")` 装饰
- 任务内自行创建 `SessionLocal()`，用后 `close()`
- 长时间操作在任务开头更新状态为 `"running"`
- 异常必须捕获并持久化到 DB (`error_message` + `status="failed"`)
- 幂等：同一事件重复执行不产生副作用

详见 [.claude/standards/tasks.md](.claude/standards/tasks.md)

### 测试规范

- 框架: pytest + pytest-cov
- 测试文件放在 `tests/` 目录，命名 `test_<module>.py`
- 共享 fixture 放 `conftest.py`
- 每个测试函数只测一个行为
- 外部依赖 (LLM API, Git, Roslyn) 使用 Mock

详见 [.claude/standards/testing.md](.claude/standards/testing.md)

### 新增功能模块

新增模块的标准流程与文件模板见 [.claude/standards/new-module.md](.claude/standards/new-module.md)

## 关键设计决策 (不可随意修改)

1. **Roslyn 容器化** — 独立 .NET 8 Docker 容器，Python 通过 HTTP `POST /analyze` 调用
2. **三轮评估流水线** — R1(规则汇总) → R2(LLM语义审查) → R3(LLM综合评分)，每轮独立持久化
3. **通知与发送分离** — 通知服务仅构建消息+入库，实际发送由外部工具平台完成
4. **幂等设计** — webhook 通过 `X-GitHub-Delivery` 幂等；RuleResult 通过 `event_id + scan_type` 幂等重写
5. **演示降级** — Roslyn NuGet 分析器包运行时不可用时，通过 `seed_demo_data.py` 注入模拟数据

## 禁止事项

- 未经用户明确授权，不允许创建或修改 `.md`/`.txt` 等文档类文件 (`.memory/` 目录除外)
- 不允许在未确认前进入批量修改、整体重构或大规模代码重写
- 不允许臆造项目结构、配置状态或依赖情况
- 修改必须在 `pwsh` 环境中执行
