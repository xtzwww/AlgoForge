# AGENTS.md — AlgoForge 项目宪法

> 技术栈约束、工程红线与开发规范。所有贡献者在动工前必读。

## 一、技术栈锁定

以下选型已确定，不得擅自更换。如需变更，须在此文件更新并说明理由。

### 前端（desktop/）

| 项 | 选型 | 约束 |
|---|---|---|
| 桌面壳 | Electron 33+ | 不用 Tauri |
| 前端框架 | Vue 3 `<script setup>` + Composition API | 不用 React |
| 语言 | TypeScript | 不用 JS |
| **UI 组件库** | **Naive UI 2.45+** | **全量组件库依赖。不用 Element Plus / Ant Design Vue / shadcn-vue** |
| 主题桥接 | `components/naive-theme.ts` | 组件库配色**只从这里来**，见下节 |
| CSS | Tailwind CSS | 不用 Sass / 手写 CSS。布局用 Tailwind，**控件外观不再手写** |
| 编辑器 | Monaco Editor | 不用 CodeMirror |
| 终端面板 | 普通 `<div>` + WebSocket | **不用 xterm.js** |
| 可视化 | ECharts + vue-echarts | 不用 Chart.js / D3 |
| 状态管理 | Pinia | 不用 Vuex |
| 构建 | electron-vite（vue-ts 模板） | — |

### 后端（sidecar/）

| 项 | 选型 | 约束 |
|---|---|---|
| 语言 | Python 3.11+ | — |
| Web 框架 | FastAPI + Uvicorn | 不用 Flask / Django |
| 数据校验 | Pydantic v2 | — |
| ORM | SQLAlchemy 2.0 (async) | 不用 Tortoise / 裸 SQL |
| 迁移 | Alembic | — |
| 业务数据库 | PostgreSQL | 不用 SQLite / MySQL（与 LangGraph 检查点同库） |
| 向量检索 | Milvus | **不用 sqlite-vec / Chroma / Pinecone / Weaviate** |
| Embedding | LangChain Embedding 接口（bge-m3 或 OpenAI 兼容） | — |
| Agent 编排 | LangGraph + LangChain 1.x（create_agent + middleware） | **不用 Autogen / CrewAI** |
| LangGraph 检查点 | PostgreSQL（生产）/ InMemorySaver（开发） | 课程2: 56-83 |
| 长期记忆 Store | PostgreSQL Store（生产）/ InMemoryStore（开发） | 课程2: 78-83 |
| LLM 接口 | OpenAI 兼容协议 | 主路径 DeepSeek API，兜底 Ollama |
| 沙箱 | subprocess + 超时 + 内存限制 | **不自研 setrlimit / 不用 Docker** |
| 教学模型 | 知识点正确率统计 + SM-2 | **不用 BKT**（降级简化） |

### 工具链

| 用途 | 工具 |
|---|---|
| 前端规范 | ESLint + Prettier |
| 后端规范 | Ruff |
| 前端测试 | Vitest |
| 后端测试 | pytest |
| 打包（前端） | electron-builder |
| 打包（后端） | PyInstaller (onedir) |

### UI 组件库：为什么是 Naive UI（2026-09-26 修订）

原选型是 shadcn-vue「模板复制模式，不用 npm 组件库依赖」。这条在本次修订中**被推翻**，
理由是它和项目里更早的一个决定冲突，而且冲突的代价落在每一屏上。

冲突点：本项目有**三套手工雕的主题**（`neon` 默认玻璃暗色 / `terminal` 近黑琥珀 /
`paper` 浅暖纸），全部以 CSS 自定义属性 `--af-*` 存在 `assets/themes.css`，且有
`assets/tokens.css`（CSS 用）与 `assets/tokens.ts`（ECharts / Monaco 用）两份镜像。
shadcn-vue 是复制进仓库的源码，本身**不认识** `--af-*`；要让它吃上三套主题，得把每个
被复制的组件逐个改写成读 `--af-*`——等于自己维护一个组件库，正是"模板复制模式"号称
要避免的事。而当时仓库里其实**一个 shadcn 组件都没装**（`components/ui/` 不存在），
所以这条约束的真实状态是「写在纸上、没在执行」，同时页面控件确实是手写的
（`.af-t-primary` / `.af-t-ghost` / `.refresh-btn` / `.filter-chip` 等各写各的），
视觉不统一就是这么来的。

选 Naive UI 的理由：它的配色是**配置出来的**——一份 JS 对象 `themeOverrides`，
逐项指名颜色 / 圆角 / 字号。于是可以写一层桥（`components/naive-theme.ts`），
把三套 `--af-*` 值喂进去，组件库与页面其余部分**共用同一批颜色**。这是
CSS 变量驱动的组件库做不到的（它们要么不吃外部变量，要么得逐个打补丁）。

代价，写在明面上：
1. **配色现在有三处副本**：`themes.css`、`tokens.ts`、`naive-theme.ts`。
   改主题色必须同时改这三处，漏改的那一处会静默不一致（不报错）。
   三个文件头都写了提示。
2. **`themeOverrides` 里必须用字面色值**（`#eef2ff`），不能写 `var(--af-text)`。
   Naive 内部对颜色做运算（算 hover 态、派生色、透明度），喂字符串会算成透明或黑色。
3. Naive UI 自带 `darkTheme` 只负责**是非明暗**那半边；具体色值全由 `themeOverrides`
   覆盖，所以 `terminal` 和 `neon` 同用 `darkTheme`，靠覆盖值区分。

边界（不因此松动的部分）：
- 布局仍用 Tailwind；组件库只接管**控件外观**，不接管排版。
- 页面级皮肤（`assets/teacher.css` 等）保留，但**只允许留布局与容器**；
  按钮、输入框、弹窗、下拉、表格一律用 Naive 组件，不新写控件级 CSS。
  已在用但属于控件的那几条（见上）随改造逐个删掉。
- 改造顺序：**教师端先行**（5 个面板），学生端随后。未改造的旧页面允许暂时共存。

## 二、工程红线

### 不可做的事

1. **不引入消息队列**：不依赖 Redis、RabbitMQ、Celery。单用户场景 asyncio 足够。
2. **向量检索只用 Milvus**：不用 sqlite-vec / Chroma / Pinecone / Weaviate。
3. **不引入 Docker 作为沙箱方案**：用 subprocess + 资源限制，够判 AC/WA/TLE 即可。Docker 仅用于运行 Milvus。
4. **不在 S0–S6 阶段做打包**：PyInstaller / electron-builder 是 S10 的事，dev 模式演示即可。
5. **不先做 Multi-agent 再做单 Agent**：S2 单次调用 → S4 LangGraph 编排 → S7 Multi-agent，顺序不可跳。
6. **Agent 编排用 LangGraph 状态图或 LangChain 1.x create_agent**：两者同生态可混用，不用 Autogen / CrewAI。
7. **不在沙箱里执行未受信任的代码而不设超时**：每次执行必须有超时 + 内存上限。
8. **不在 prompt 里硬编码业务逻辑**：所有 prompt 走 `sidecar/app/prompts/` 模板文件，版本化管理。
9. **不手写控件级 CSS**：按钮 / 输入框 / 弹窗 / 下拉 / 表格用 Naive UI 组件。要换皮肤改 `naive-theme.ts`，不写 `.my-btn { ... }`。
10. **不在 `themeOverrides` 里写 `var(--af-*)`**：Naive 对颜色做运算，`var()` 会静默算成透明/黑色。

### 必须做的事

1. **所有 LLM 输出必须过 JSON Schema 校验**：不合规则重试或降级，绝不入库。
2. **LLM Gateway 统一 OpenAI 兼容协议**：换模型只改配置，不改代码。
3. **数据库迁移走 Alembic**：不手写 `ALTER TABLE`。
4. **Sidecar 监听随机端口 + Token 鉴权**：不硬编码端口。
5. **S2 是 go/no-go 节点**：20 道真题验证错因分析准确率不达标则调整方案（换模型 / 加 RAG / 调 prompt），不硬推后续阶段。
6. **前端代码逐文件 TS 严格模式**：不用 `any` 逃避类型。
7. **弹窗类交互用 `NModal` / `NPopconfirm`**：不用 `window.confirm` / `window.alert`（阻塞、样式不受控、不可测）。

## 三、阶段约束

每个阶段有明确的"完成定义"和"准入条件"。

### S0 脚手架

- **完成定义**：Electron 窗口能打开，Monaco 能编辑 C++ 文件，Sidecar /health 返回 200，两者通过 localhost 通信。
- **准入 S1**：双进程通信跑通，Monaco 语言服务加载正常。

### S1 判题闭环

- **完成定义**：编辑 C++ → 编译 → 运行 → 输出对比 → 判定 AC/WA/TLE。日志通过 WebSocket 流式推送到前端面板。
- **准入 S2**：至少 3 道题跑通完整判题流程（含 AC、WA、TLE 各一）。

### S2 单次 Agent（go/no-go 节点）

- **完成定义**：WA 时调 DeepSeek API，输出符合 JSON Schema 的结构化错因标签 + 修正提示。LLM Gateway 可切换 Ollama。
- **准入 S3**：**20 道真题错因分析准确率 ≥ 60%**。不达标则：调 prompt → 加 RAG（提前进 S5）→ 换模型 → 降标准。这是整个项目的生死线。

### S3 Function Calling

- **完成定义**：Agent 能自主决定调用 `run_code()`、`judge()` 等工具函数，工具结果回传给 Agent。
- **准入 S4**：至少 2 个工具被 Agent 成功调用。

### S4 LangGraph 编排

- **完成定义**：S2 的单次调用改造为 LangGraph 状态图：读代码节点 → 检索节点（占位）→ 分析节点 → 自检节点。支持条件分支。
- **准入 S5**：状态图至少包含 3 个节点 + 1 个条件分支，能跑通一次完整流程。

### S5 RAG

- **完成定义**：LangChain Embedding + Milvus 检索算法知识库，检索结果组装进 Agent 上下文。
- **准入 S6**：检索 Top-3 结果中至少 1 条与题目知识点相关（人工验证）。

### S6 ReAct 反思

- **完成定义**：Agent 分析后自检输出，发现不准则回到检索/分析节点重试。循环有最大次数限制（建议 3 次）。
- **准入 S7**：自检循环能在 3 次内收敛（不再重试）。

### S7–S10（扩展阶段）

- **S7 Multi-agent**：错因/提示/变式题三 Agent 协作。
- **S8 Memory + Streaming**：短期记忆（InMemorySaver/PostgreSQL 检查点）+ 长期记忆（PostgreSQL Store）+ 前端流式渲染（LangGraph stream）。
- **S9 Eval + Observability**：评估集 + LangSmith 追踪。
- **S10 Guardrails + 收尾**：LangChain 1.x 中间件护栏（PIIMiddleware/ModelFallback/ModelRetry 等）+ 画像 + 错题本 + SM-2 + 打包。
- **约束**：S0–S6 未完成不启动 S7+。

## 四、目录与命名规范

### 目录职责

| 目录 | 职责 | 不应包含 |
|---|---|---|
| `desktop/src/main/` | Electron 主进程 | 渲染逻辑 |
| `desktop/src/renderer/src/` | Vue 前端 | 后端逻辑 |
| `sidecar/app/api/` | REST 路由 + WebSocket | 业务逻辑（应下沉到 agent/ 或 sandbox/） |
| `sidecar/app/agent/` | LangGraph 图、节点、状态 | HTTP 路由 |
| `sidecar/app/rag/` | Milvus 向量检索、语料切分嵌入 | HTTP 路由 |
| `sidecar/app/prompts/` | Prompt 模板文件 | Python 逻辑 |
| `corpus/` | 算法知识库语料 | 代码 |

### 命名

- Python 文件：`snake_case.py`
- Vue 组件文件：`PascalCase.vue`
- TypeScript 文件：`camelCase.ts` 或 `kebab-case.ts`
- 数据库表名：`snake_case`（如 `submissions`、`mistake_book`）
- API 路由：`/api/v1/kebab-case`（如 `/api/v1/run-code`）

## 五、Git 规范

### 分支

- `main`：稳定分支
- `dev`：开发集成
- `feature/s{阶段号}-{简述}`：如 `feature/s0-scaffold`、`feature/s2-agent-call`

### 提交信息

```
<type>(<scope>): <subject>

<body>
```

- type：`feat` / `fix` / `refactor` / `docs` / `test` / `chore`
- scope：`frontend` / `backend` / `agent` / `sandbox` / `docs`
- 示例：`feat(agent): implement LangGraph error analysis graph`

## 六、环境变量

开发环境使用 `.env` 文件，不入库。`.env.example` 作为模板。

```env
# LLM
LLM_PROVIDER=deepseek          # deepseek | ollama | openai_compatible
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-coder

# Ollama（可选）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b

# 其他 OpenAI 兼容端点（Agnes AI / vLLM / LM Studio …）
# 设 LLM_PROVIDER=openai_compatible 并填这三个值即可接入，不改代码。
# 若服务需要版本段，请把它写进 BASE_URL——网关只在 URL 不带版本段时
# 才补 `/v1`。KEY 可留空（自建无鉴权服务是合法的），留空时不发该头。
# 两种拼写 openai_compatible / openai-compatible 都接受。
OPENAI_COMPATIBLE_BASE_URL=
OPENAI_COMPATIBLE_API_KEY=
OPENAI_COMPATIBLE_MODEL=

# 分析调用的回复 token 上限。是上限不是目标：模型自己写完就停，只按实际
# 用量计费，调高对简洁模型零成本；调太低则是硬失败——回复被截断，JSON
# 解析器只会报「不是合法 JSON」，把预算问题误报成格式问题。
# 推理模型（如 agnes-2.5-flash）的隐藏推理同样计入该预算，需调高约一个
# 数量级（实测 16000 才收敛，8000 时返回空内容）。
LLM_MAX_TOKENS=4096

# 分析调用的单次请求超时（秒）。同样取决于模型：托管端点生成数千 token 比
# 本地 3B 模型写一句话慢得多。设太短不会报配置错误，而是以
# `LLMConnectionError: timed out` 出现，并被 P2-5 计为「模型答错」——
# 实测 60s 默认值下 20 道用例里丢了 2 道。
LLM_TIMEOUT=60

# LangSmith（可选）
LANGSMITH_API_KEY=lsvt_xxx
LANGSMITH_PROJECT=algoforge

# Sidecar
SIDECAR_PORT=                  # 留空则随机
SIDECAR_TOKEN=                 # 留空则随机生成（只生成一次，之后固定）

# 侧车会校验它：HTTP 请求要带 `X-Sidecar-Token` 头，WebSocket 握手要带
# `?token=`，两者取同一个值；`/health` 与 CORS 预检豁免。所以客户端必须拿到
# 这个值——Electron 由主进程生成并注入，网页版走 `VITE_SIDECAR_TOKEN`。
# 它是一个共享密钥，证明「是本应用」，不区分使用者。

# 同时最多跑几个编译/判题（沙箱线程池大小）。留空用默认 4。
# 这是上限不是目标值：超出的请求会排队等待，不会被拒绝。
SANDBOX_CONCURRENCY=

# PostgreSQL（业务数据 + LangGraph 检查点 + 长期记忆 Store）
DATABASE_URL=postgresql+asyncpg://algoforge:algoforge@localhost:5432/algoforge
LANGGRAPH_CHECKPOINT_URI=postgresql://algoforge:algoforge@localhost:5432/algoforge

# Milvus（RAG 向量检索）
MILVUS_URI=http://localhost:19530
```
