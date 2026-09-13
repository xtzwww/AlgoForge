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
| 测试 | Vitest（前端）/ pytest（后端） |

## 系统架构

```
┌─────────────────────── Electron 桌面壳 ───────────────────────┐
│  Renderer (Vue 3 + TS)                                        │
│  ├─ Monaco Editor / 输出面板 / ECharts 画像                   │
│  ├─ Pinia 状态管理 / Vue Router                               │
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

## 目录结构

```
algoforge/
├─ desktop/                  # Electron + Vue 3 客户端
│  └─ src/
│     ├─ main/               # 主进程：spawn sidecar、IPC、窗口管理
│     ├─ preload/            # contextBridge：注入 sidecar 地址与 Token
│     └─ renderer/src/
│        ├─ views/           # Practice / Mistakes / Profile / Settings
│        ├─ components/      # CodeEditor / OutputPanel / RadarChart / HintCard
│        ├─ stores/          # Pinia: editor / sandbox / agent / profile
│        ├─ api/sidecar.ts   # axios + WebSocket 封装
│        └─ composables/     # useRunStream / useLLMStream
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
> 本地跑脚本启动时为了日志和调试方便，默认固定用 8000。

### 启动开发环境

```bash
# 1. 启动 Milvus（Docker）
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest

# 2. 启动 PostgreSQL
# Windows: 安装后确保服务运行，创建数据库 algoforge

# 3. 后端
cd sidecar
pip install -r requirements.txt
alembic upgrade head              # 读取 DATABASE_URL，无需改 alembic.ini
uvicorn app.main:app --reload --port 8000

# 4. 前端
cd desktop
npm install
npm run dev
```

也可以直接用根目录的一键脚本：`start.bat` 或 `start.ps1`。

### 测试

```bash
cd sidecar
python -m pytest ../tests -q                  # 全部（含编译 C++，约 6 分钟）
python -m pytest ../tests -q -m "not eval_set" # 跳过慢用例，约 1.5 分钟
```

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
