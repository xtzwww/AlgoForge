# AlgoForge

**基于 AI Agent 的算法竞赛错因分析桌面系统**
*Forge Your Algorithm Skills — Analyze, Track, Improve.*

## 项目定位

以算法竞赛错因分析为载体，系统实践现代 AI Agent 开发的完整技术栈。项目核心价值不在"又一个 OJ"，而在于**亲手实现从 LLM 调用到多步 Agent 编排的全链路工程**。

### 教学闭环

```
运行 → 判题 → 诊断 → 追踪 → 调度
```

- **运行**：Monaco Editor 编写 C++，一键编译执行
- **判题**：沙箱执行 + 输出对比，判定 AC / WA / TLE / MLE / CE / RE
- **诊断**：LangGraph Agent 多步分析错因，输出结构化标签 + 苏格拉底式提示
- **追踪**：按知识点统计正确率，ECharts 雷达图展示能力画像
- **调度**：SM-2 间隔重复算法调度复习时点，错题自动归档

## AI 技术栈

| 层 | 技术 | 项目中的作用 |
|---|---|---|
| 模型地基 | LLM Gateway（OpenAI 兼容协议） | 统一 DeepSeek API + Ollama 接口，本地/云端一键切换 |
| 调用基本功 | Prompt 模板版本化 + JSON Schema 校验 | 提示词文件化管理；Agent 输出强校验，保证可渲染可入库 |
| 工具与检索 | Function Calling + RAG（Milvus + LangChain Embedding） | Agent 自主调判题器/执行代码；检索算法知识库辅助分析 |
| Agent 大脑 | LangGraph + LangChain 1.x（create_agent + middleware）+ ReAct | 状态图编排多步推理：读代码→检索→分析→自检→不准则重试 |
| 协作与记忆 | Multi-agent + Memory（PostgreSQL Store） | 错因/提示/变式题 Agent 分工；短期对话记忆 + 长期错题画像记忆 |
| 交付与支撑 | Streaming（LangGraph stream）+ Guardrails（中间件）+ Eval + LangSmith | 流式显示思考过程；护栏降级；离线评估集；LangSmith 链路追踪 |

### 关于 LangChain 与 LangGraph 的关系

LangGraph 是 LangChain 团队专为 Agent 设计的产品，属于 LangChain 生态。LangChain 1.x 在 LangGraph 之上封装了 `create_agent` + middleware 高层 API，两者同生态可混用：简单 Agent 用 `create_agent` + 中间件（PIIMiddleware/ModelFallback 等），需要精细控制流程时裸用 `StateGraph`（手写节点、边、条件分支、循环）。本项目两者并用：S2-S3 用 `create_agent`，S4 裸用 `StateGraph` 实现多步图。

## 非 AI 技术栈

| 层 | 技术 | 项目中的作用 |
|---|---|---|
| 桌面壳 | Electron 33+ | 内嵌 Web UI，spawn 并守护 Python sidecar |
| 后端 | Python 3.11 + FastAPI + Uvicorn + Pydantic v2 | 异步 API + WebSocket 日志流，原生对接 AI 库 |
| 前端框架 | Vue 3（`<script setup>` + Composition API）+ TypeScript | UI 层 |
| UI 组件 | shadcn-vue + Tailwind CSS | 模板复制模式，组件代码在手里可改 |
| 编辑器 | Monaco Editor | IDE 式 C++ 代码编辑，项目卖点核心 |
| 输出面板 | 普通 `<div>` + WebSocket | 流式显示编译/运行日志 |
| 可视化 | ECharts + vue-echarts | 能力雷达图 + 成长趋势图 |
| 数据库 | PostgreSQL | 业务数据 + LangGraph 检查点 + 长期记忆 Store 同库 |
| 向量数据库 | Milvus（Docker 运行） | RAG 向量检索 |
| ORM | SQLAlchemy 2.0 (async) + Alembic | ORM 操作 + 数据库迁移版本控制 |
| 沙箱 | subprocess + 超时 + 内存限制 | 判题执行隔离，够用即可 |
| 打包 | electron-builder + PyInstaller (onedir) | 延后到最后阶段，dev 模式演示 |

## 开发工具链

| 用途 | 工具 |
|---|---|
| 前端构建 | electron-vite（vue-ts 模板） |
| 后端依赖 | uv 或 pip + requirements.txt |
| 代码规范 | ESLint + Prettier（前端）/ Ruff（后端） |
| 测试 | pytest（后端，677 个用例）/ Vitest（前端，**已定但尚未接入**） |

## 系统架构

```
┌─────────────────────── Electron 桌面壳 ───────────────────────┐
│  Renderer (Vue 3 + TS)                                        │
│  ├─ Monaco Editor / 输出面板 / ECharts 画像                   │
│  ├─ Vue Router（状态用 ref，未引入 Pinia）                    │
│  └─ axios + WebSocket ──┐                                     │
│  Main 进程: spawn & 守护 sidecar、工具链探测、IPC             │
└─────────────────────────┼─────────────────────────────────────┘
                 localhost:随机端口 + Token
┌─────────────────────────┼─────────────────────────────────────┐
│  Sidecar (Python / FastAPI)  ← PyInstaller 打包，随应用分发    │
│  ├─ REST API + WebSocket 日志流                               │
│  ├─ LangGraph + LangChain 1.x Agent（错因分析 / 提示 / 变式题）│
│  ├─ LLM Gateway ──→ DeepSeek API（主）/ Ollama（离线兜底）    │
│  ├─ RAG: LangChain Embedding + Milvus 向量检索                 │
│  ├─ Memory: InMemorySaver/PG 检查点 + InMemoryStore/PG Store  │
│  ├─ Sandbox: subprocess + 资源限制 + psutil                    │
│  ├─ 教学模型: 知识点正确率统计 + SM-2 复习调度                 │
│  └─ PostgreSQL + SQLAlchemy 2.0 + Alembic                     │
└───────────────────────────────────────────────────────────────┘
```

### 架构决策要点

- **双进程分离**：UI（Electron）与计算（Python Sidecar）解耦，各自使用生态最擅长的技术栈
- **不引入消息队列**：不依赖 Redis / RabbitMQ / Celery，单用户场景 asyncio 足够
- **模型可插拔**：LLM Gateway 统一 OpenAI 兼容协议，本地/云端一键切换
- **检查点可恢复**：LangGraph 检查点存 PostgreSQL，Agent 中断后可恢复执行

### 实现状态（截至 2026-09-18）

⚠️ **上面那张图是目标架构，不等于已实现的部分。** 逐项列出来，免得误读：

| 部分 | 状态 |
|---|---|
| 判题沙箱（C++ / Python / Java，超时 + 内存上限） | ✅ 可用 |
| Monaco 编辑器（自定义主题、语言切换、快捷键） | ✅ 可用 |
| LLM Gateway（OpenAI 兼容；主路径 DeepSeek，可切 Ollama） | ✅ 可用 |
| 错因分析 Agent（12 个封闭标签 + 严格 JSON Schema 校验） | ✅ 可用 |
| Agent 工具调用（`run_code` / `judge`，结果回传模型） | ✅ 可用 |
| 错题本（自动归档、分析状态可见、失败可重试） | ✅ 可用 |
| 知识点正确率统计、SM-2 复习调度 | ✅ 已实现 |
| 业务页面（练习 / 题库 / 错题本 / 画像 / 计划） | ✅ 已实现，⚠️ **未在真实 PostgreSQL 上目视验证过** |
| LangGraph 状态图编排（S4） | ⬜ 未做——当前是单轮调用 + 工具循环 |
| RAG / Milvus 向量检索（S5） | ⬜ 未做（`sidecar/app/rag/` 只有 `.gitkeep`） |
| ReAct 自检循环（S6）、Multi-agent（S7） | ⬜ 未做 |
| LangGraph 检查点 / 长期记忆 Store（S8） | ⬜ 未做 |
| LangChain 1.x 中间件护栏（S10） | ⬜ 未做 |
| 打包（PyInstaller / electron-builder） | ⬜ 未做，目前 dev 模式运行 |
| 用户体系与鉴权 | ⬜ 未做（全部按「单机单用户」假设） |
| 前端单元测试（Vitest） | ⬜ 未接入（后端 677 个用例） |

## 目录结构

```
algoforge/
├─ desktop/                  # Electron + Vue 3 客户端
│  └─ src/
│     ├─ main/               # 主进程：spawn sidecar、IPC、窗口管理
│     ├─ preload/            # contextBridge：注入 sidecar 地址与 Token
│     └─ renderer/src/
│        ├─ views/           # Practice / ProblemList / ProblemPractice …
│        ├─ components/      # CodeEditor / OutputPanel / RadarChart / VerdictBadge …
│        ├─ api/             # judge / ws / problems / languages / mistake_book …
│        ├─ composables/     # usePageCrumb / useSidecarStatus
│        ├─ router/          # 路由表
│        └─ runtime/         # electron / web 两套运行时适配器
├─ sidecar/                  # Python FastAPI 服务
│  └─ app/
│     ├─ api/                # REST 路由 + WebSocket
│     ├─ agent/              # LangGraph 图定义、节点、状态
│     ├─ sandbox/            # 编译运行、资源限制、判题
│     ├─ rag/                # Milvus 向量检索、语料切分嵌入
│     ├─ models/             # SQLAlchemy ORM、SM-2 实现
│     └─ prompts/            # Prompt 模板（版本化管理）
├─ corpus/                   # 算法知识库语料（题解、知识点卡片）
├─ tests/                    # pytest 后端测试
└─ AGENTS.md                 # 项目宪法（技术栈约束与红线）
```

## 开发路线图

按 AI Agent 学习梯度排列，每阶段掌握一项核心技术。

| 阶段 | 学什么 | 交付物 | 关键验证点 |
|---|---|---|---|
| S0 脚手架 | 工程基础 | Electron↔Sidecar /health 联通，Monaco 编辑 C++ | 双进程通信跑通 |
| S1 判题闭环 | subprocess 沙箱 | 编辑→编译→运行→AC/WA/TLE 判定 | 跑通一道完整题 |
| S2 单次 Agent | LLM Gateway、Prompt 模板、JSON Schema 校验 | WA 调 DeepSeek，输出结构化错因标签 | 20 道真题验证准确率（go/no-go） |
| S3 Function Calling | 工具定义、Agent 调判题器/代码执行 | Agent 能自主调用工具 | 工具调用链路跑通 |
| S4 LangGraph 编排 | 状态图、节点、边、条件分支、检查点 | 单次调用改造为多步图 | Agent 多步推理跑通 |
| S5 RAG | 嵌入、向量检索、上下文组装 | Agent 检索算法知识库辅助分析 | 检索准确率可接受 |
| S6 ReAct 反思 | 自纠错循环、重试机制 | Agent 分析后自检，不准则重试 | 自检循环收敛 |
| S7 Multi-agent | Agent 分工协作、消息传递 | 错因/提示/变式题三 Agent 协作 | 多 Agent 协作跑通 |
| S8 Memory + Streaming | 短期/长期记忆、流式输出 | 记忆机制 + 前端实时显示思考过程 | 流式渲染无卡顿 |
| S9 评估 + 可观测性 | 评估集、指标、链路追踪 | 离线评估报告 + Langfuse 追踪 | 评估指标达标 |
| S10 Guardrails + 收尾 | 护栏、降级、画像、错题本、SM-2 | 完整闭环演示 | 全链路演示通过 |

**S0–S6 为核心路径**，跑通即扎实毕设。S7–S10 作为已规划扩展，做到哪个算哪个。

## 快速开始

### 前置要求

- Node.js ≥ 20
- Python ≥ 3.11
- g++ 或 clang（MinGW / MSVC / clang 任一，路径通过 `.env` 配置）
- Docker（运行 Milvus）
- PostgreSQL
- （可选）Ollama + `qwen2.5-coder:7b`

### 配置

```bash
cd sidecar
cp .env.example .env
```

然后编辑 `.env`，**至少**要改这一项——指向你本机的 g++：

```env
CPP_COMPILER_PATH=D:\BaiduNetdiskDownload\dev\ucrt64\bin\g++.exe
```

> 留空写作 `CPP_COMPILER_PATH=g++` 也可以，此时从 PATH 解析。
> `CPP_COMPILER_BIN_DIR` 默认取 g++ 所在目录（MinGW 运行 DLL 需要它），一般无需设置。
>
> `SIDECAR_PORT` / `SIDECAR_TOKEN` 留空 = 启动时随机生成（AGENTS.md 要求）。
> 正常启动路径下无需设置：Electron 主进程会自己挑一个空闲端口并生成 Token，
> 再通过 IPC 传给渲染进程（见 `desktop/src/main/sidecar.ts`）。
> 只有想手动 `curl` 调试后端时才需要显式固定端口。

### 启动开发环境

```bash
# 1. 启动 Milvus（Docker）
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest

# 2. 启动 PostgreSQL
# Windows: 安装后确保服务运行，创建数据库 algoforge
# 未启动时应用仍可运行，只是错题本/画像/复习无数据持久化；
# 可用 GET /api/v1/env-check 查看各组件是否就绪。

# 3. 后端依赖 + 建表（只做一次）
cd sidecar
pip install -r requirements.txt
alembic upgrade head              # 读取 DATABASE_URL，无需改 alembic.ini

# 4. 前端（会一并拉起 sidecar）
cd desktop
npm install
npm run dev
```

> **sidecar 不需要单独启动。** 第 4 步的 `npm run dev` 启动 Electron 后，
> 主进程会用随机端口 + 随机 Token 拉起 sidecar，等 `/health` 通过再显示窗口，
> 退出时连同 uvicorn 的子进程一起清理。sidecar 日志以前缀 `[sidecar]`
> 混在 Electron 的终端输出里。

也可以直接用根目录的一键脚本：`start.bat` 或 `start.ps1`。
两个脚本现在只负责「把 Node 加进 PATH + 交给 `npm run dev`」，
不再各自启动一份 sidecar（旧版会因此多开一个控制台窗口，且与 Electron
的启动参数不一致）。

### 测试

```bash
cd sidecar

# 日常用这条：跳过 eval_set，约 9 分钟
env -u PYTHONPATH python -m pytest ../tests -q -m "not eval_set"

# 只在确实要测真实模型时跑：会调用付费端点、按测试计费、结果不可复现
env -u PYTHONPATH python -m pytest ../tests -q -m eval_set
```

三点说明，都是实测踩过的：

- **`env -u PYTHONPATH` 不能省。** 否则 `sitecustomize.py` 会让清理阶段的
  `Path.unlink` 抛 `SystemExit(1)`，表现为一大批与本次改动无关的失败。
- **默认不要跑全量。** `eval_set` 那批（S2 的 20 道真题 + Function Calling 实机测试）
  会真的请求 LLM 服务，既计费又不可复现——**不要把它当成「跑个全量」的顺手选项**。
- 慢的是**真实 C++ 编译**（单文件约 88 秒），不是 LLM。当前基线
  **677 passed / 1 skipped / 8 deselected**。

前端目前没有单元测试（`AGENTS.md` 锁定 Vitest 作为前端测试框架，尚未接入）；
前端校验目前靠类型检查 + ESLint，见 `.github/workflows/ci.yml`。

### 评估集校验

`corpus/eval_cases/` 中 20 道 S2 真题用例需满足：参考解必须 AC，
错误解必须复现标注的失败类型。改动用例后务必重新校验：

```bash
python tools/validate_eval_cases.py    # 全量编译运行校验
python tools/generate_eval_cases.py    # 需要时重新生成用例
```

### 数据库说明

`DATABASE_URL` 是生产 PostgreSQL DSN（`postgresql+asyncpg`）。
API 路由是同步的，`app/db.py` 会把 asyncpg URL 自动改写为
`psycopg` 同步驱动——所以**不会**再静默降级到 SQLite。
同步路由需要指向另一个 DSN 时，用 `DATABASE_URL_SYNC` 覆盖。
测试通过 monkeypatch `get_session` 使用 SQLite，无需真实 PG。

### 生产打包

```bash
npm run build        # 产出 Windows / macOS / Linux 安装包
```

## 许可

MIT
