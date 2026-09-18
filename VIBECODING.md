# 基于 AI Agent 的编程课程学习过程分析与反馈系统 — Vibecoding 指南

> 本文件指导如何用 AI 辅助开发完成本项目。核心原则：**小切片、可验证、不跳步**。
>
> ⚠️ **历史遗留文件。** 本文件写于项目早期（定位为「算法竞赛错因分析系统」时），
> 其中的阶段划分（Phase 1 / Phase UX / Phase 2 及 P2-x 切片号）
> 与当前「教学学习反馈」方向不再对应。**保留作为开发方法的参考，
> 但不要按其中的阶段顺序推进**——当前路线图以 `README.md` 第六节为准。

## 一、Vibecoding 工作规范

### 1. 一次只做一件事

每次给 AI 的任务描述只包含**一个可验证的功能单元**。不要一次说"帮我做完整的判题系统"，而是拆成"创建 FastAPI /health 路由"、"实现编译 C++ 的函数"、"实现输出对比判题函数"等。

**错误示范**：
> 帮我实现 S1 判题闭环，包括编译、运行、输出对比、WebSocket 推送。

**正确示范**：
> 在 `sidecar/app/sandbox/compiler.py` 中实现 `compile_cpp(source_path) -> CompileResult` 函数，调用 g++ 编译，返回成功/失败 + 错误信息。写一个 pytest 测试验证编译成功和编译失败两种情况。

### 2. 每步产出可验证

每个切片完成后必须能**跑起来验证**，不能是"写完一堆代码下次再看"。

| 切片类型 | 验证方式 |
|---|---|
| 后端函数 | pytest 单测通过 |
| API 路由 | curl / httpie 请求返回正确 |
| 前端组件 | 浏览器/Electron 窗口能看到 |
| 全链路 | 端到端走通一个完整流程 |

### 3. 上下文先行

每次让 AI 写代码前，先让它读相关文件：
- AGENTS.md（技术约束）
- 目标目录的现有代码（保持风格一致）
- 相关接口定义（如 Pydantic model、TypeScript interface）

**提示模板**：
```
先读以下文件了解项目约束和现有代码风格：
1. AGENTS.md
2. sidecar/app/sandbox/ 目录下所有文件
3. sidecar/app/models/ 目录下所有 ORM 定义

然后在 sidecar/app/sandbox/runner.py 中实现 ...
```

### 4. 不改不相关的代码

AI 倾向于"顺手优化"。明确告诉它：**只改我说的文件，不要动其他文件，不要重构无关代码**。

### 5. 先定义接口再填实现

复杂功能分两轮：
1. **第一轮**：定义接口（Pydantic model / TypeScript interface / 函数签名），不含实现
2. **第二轮**：逐个填充实现

这样 AI 在第二轮有明确的接口契约，不会跑偏。

### 6. 测试驱动

- 后端：每个模块写完立刻补 pytest
- 前端：关键交互写 Vitest
- 不追求覆盖率，追求**每个切片有可运行的验证**

### 7. Commit 节奏

每个可验证切片完成后立即 commit。不要攒一堆改动再提交。

```
feat(backend): add compile_cpp function with tests
fix(sandbox): handle g++ not found error
feat(frontend): add monaco editor component
```

## 二、功能实现顺序

分三个阶段：**Phase 1 纯工程基建（无 AI）**，**Phase UX 前端体验/多语言/题库预准备**，
**Phase 2 AI Agent 开发**。

这样你可以在 Phase 1 期间并行学习课程，到 Phase 2 时课程基础已打好。

> **Phase UX（PU-0 ~ PU-17）详见 `docs/PHASE-UX.md`**。
> 该阶段在 P1-23 之后、P2-0 之前插入，解决 Phase 1 遗留的产品级断层：
> 启动黑框、无启动动画、无网页端、布局粗糙、题目硬编码、仅支持 C++、
> 提交记录不入库。**Phase 2 的 Agent 代码在该阶段一行不写**，只准备其输入。

> **课节说明**：C1 = 课程一 LangChain 1.x，C2 = 课程二 LangGraph。格式为 C1:节号 或 C2:节号。Phase 1 切片无需课程。

---

### Phase 1：纯工程基建（切片 P1-0 ~ P1-23）

> 不涉及任何 AI/LM 功能。完成后应用已是一个可用的 OJ + 错题本 + 画像系统，只是没有 AI 错因分析。
> 此阶段可并行学习课程，不依赖课程知识。

#### P1-0：初始化项目结构
- **产出**：创建 `desktop/`、`sidecar/`、`corpus/`、`tests/`、`docs/` 目录
- **验证**：目录结构存在
- **前置**：无

#### P1-1：FastAPI 最小骨架
- **产出**：`sidecar/app/main.py`、`sidecar/app/api/health.py`、`requirements.txt`
- **内容**：FastAPI 应用实例 + `/health` 路由返回 `{"status": "ok"}`
- **验证**：`uvicorn app.main:app --reload` 后 `curl localhost:8000/health` 返回 200
- **前置**：P1-0

#### P1-2：Pydantic 配置模型
- **产出**：`sidecar/app/config.py`
- **内容**：用 Pydantic Settings 读取环境变量（LLM_PROVIDER、DEEPSEEK_API_KEY 等），对应 AGENTS.md 的 `.env` 定义。此切片只建配置框架，不实际调 LLM。
- **验证**：写 pytest 验证默认值和环境变量覆盖
- **前置**：P1-1

#### P1-3：Electron + Vue 3 项目初始化
- **产出**：`desktop/` 下 electron-vite vue-ts 模板
- **内容**：`npm create @quick-start/electron` 选 vue-ts 模板，安装 Tailwind + shadcn-vue
- **验证**：`npm run dev` 弹出 Electron 窗口显示 Vue 欢迎页
- **前置**：P1-0

#### P1-4：Electron main 进程 spawn sidecar
- **产出**：`desktop/src/main/sidecar.ts`
- **内容**：Electron 启动时 spawn Python sidecar 进程，随机端口 + 生成 Token，通过 preload 暴露给渲染进程
- **验证**：Electron 启动后 console 能看到 sidecar 的 /health 返回
- **前置**：P1-1、P1-3

#### P1-5：preload contextBridge
- **产出**：`desktop/src/preload/index.ts`
- **内容**：通过 contextBridge 暴露 `window.algoforge` 对象，包含 sidecar 地址和 Token
- **验证**：渲染进程 `console.log(window.algoforge)` 能看到地址和 Token
- **前置**：P1-4

#### P1-6：Monaco Editor 组件
- **产出**：`desktop/src/renderer/src/components/CodeEditor.vue`
- **内容**：加载 Monaco，设置 C++ 语言，基础主题
- **验证**：窗口里能看到编辑器，能输入 C++ 代码
- **前置**：P1-3

#### P1-7：axios + WebSocket 封装
- **产出**：`desktop/src/renderer/src/api/sidecar.ts`
- **内容**：封装 axios 实例（带 Token header）+ WebSocket 连接函数
- **验证**：前端调 `/health` 成功
- **前置**：P1-5

#### P1-8：题目数据模型
- **产出**：`sidecar/app/models/base.py`（SQLAlchemy Base）、`sidecar/app/models/problem.py`
- **内容**：Problem 表（id、title、description、input_format、output_format、samples、time_limit、memory_limit、knowledge_tags）
- **验证**：Alembic 迁移成功，pytest 能插入和查询
- **前置**：P1-2

#### P1-9：提交记录模型
- **产出**：`sidecar/app/models/submission.py`
- **内容**：Submission 表（id、problem_id、code、language、status、stdout、stderr、exec_time、memory_used、created_at）
- **验证**：迁移 + CRUD 测试
- **前置**：P1-8

#### P1-10：Alembic 迁移初始化
- **产出**：`sidecar/alembic.ini`、`sidecar/alembic/` 目录
- **内容**：初始化 Alembic，生成首个迁移
- **验证**：`alembic upgrade head` 建表成功
- **前置**：P1-8、P1-9

#### P1-11：编译模块
- **产出**：`sidecar/app/sandbox/compiler.py`
- **内容**：`compile_cpp(source_path) -> CompileResult`，调 g++ 编译，处理编译错误
- **验证**：pytest 测编译成功 + 编译失败两种
- **前置**：无

#### P1-12：执行模块
- **产出**：`sidecar/app/sandbox/runner.py`
- **内容**：`run_executable(exec_path, stdin, time_limit, mem_limit) -> RunResult`，subprocess + 超时 + 内存限制
- **验证**：pytest 测正常运行 + 超时 + 内存超限三种
- **前置**：P1-11

#### P1-13：判题模块
- **产出**：`sidecar/app/sandbox/judge.py`
- **内容**：`judge(stdout, expected_output) -> JudgeResult`，对比输出，判定 AC/WA
- **验证**：pytest 测完全匹配、空格容错、不匹配三种
- **前置**：无

#### P1-14：判题 API 路由
- **产出**：`sidecar/app/api/judge.py`
- **内容**：`POST /api/v1/run-code` 接收代码 + 题目 ID，走编译→运行→判题，返回结果
- **验证**：curl 提交一段 C++ 代码返回 AC/WA
- **前置**：P1-10、P1-11、P1-12、P1-13

#### P1-15：WebSocket 日志流
- **产出**：`sidecar/app/api/ws.py`
- **内容**：`WS /ws/run-log` 连接后，判题过程中流式推送编译/运行日志
- **验证**：前端连上 WS 后能看到日志逐行出现
- **前置**：P1-14

#### P1-16：前端练习页面
- **产出**：`desktop/src/renderer/src/views/PracticeView.vue`、`desktop/src/renderer/src/components/OutputPanel.vue`
- **内容**：Monaco 编辑器 + 运行按钮 + 输出面板，点击运行调 API + 接 WS 日志
- **验证**：窗口里写代码、点运行、看到输出和判题结果
- **前置**：P1-6、P1-7、P1-15

#### P1-17：知识点正确率统计
- **产出**：`sidecar/app/models/knowledge_stats.py`、统计查询函数
- **内容**：按知识点聚合提交记录，计算正确率
- **验证**：模拟若干提交后，查询返回正确统计
- **前置**：P1-9

#### P1-18：错题本模型 + API
- **产出**：`sidecar/app/models/mistake.py`、`sidecar/app/api/mistakes.py`
- **内容**：错题表（关联 submission + error_type 字段**先留 nullable**，AI 接入后填充）+ CRUD API
- **验证**：API 能增删查错题
- **前置**：P1-9
- **注意**：error_type 字段此时为 null，Phase 2 的 P2-4 会接入 AI 错因分析后回填

#### P1-19：SM-2 复习调度
- **产出**：`sidecar/app/models/review.py`、`sidecar/app/scheduler/sm2.py`
- **内容**：SM-2 算法实现 + 复习记录表 + 计算下次复习时间
- **验证**：pytest 验证不同质量分下的下次复习间隔
- **前置**：P1-18

#### P1-20：能力画像 API
- **产出**：`sidecar/app/api/profile.py`
- **内容**：返回各知识点正确率 + 雷达图数据
- **验证**：curl 返回 JSON 格式正确
- **前置**：P1-17

#### P1-21：前端错题本页面
- **产出**：`desktop/src/renderer/src/views/MistakesView.vue`
- **内容**：错题列表 + 按知识点筛选（错因筛选待 AI 接入后启用）
- **验证**：页面能看到错题列表，知识点筛选有效
- **前置**：P1-16、P1-18

#### P1-22：前端能力画像页面
- **产出**：`desktop/src/renderer/src/views/ProfileView.vue`、`desktop/src/renderer/src/components/RadarChart.vue`
- **内容**：ECharts 雷达图 + 趋势图
- **验证**：雷达图正确显示各知识点掌握度
- **前置**：P1-16、P1-20

#### P1-23：前端复习计划页面
- **产出**：`desktop/src/renderer/src/views/PlanView.vue`
- **内容**：今日待复习列表 + 标记复习质量
- **验证**：能看到今日复习项，标记后下次复习时间更新
- **前置**：P1-19

---

### Phase 1 完成检查点

| 检查点 | 时机 | 检查内容 | 不通过怎么办 |
|---|---|---|---|
| 通信验证 | P1-4 完成 | Electron↔Sidecar 能互调 | 排查端口/Token/spawn 问题 |
| 判题准确性 | P1-14 完成 | 3 道题 AC/WA/TLE 判定正确 | 检查输出对比逻辑、超时设置 |
| 全前端可用 | P1-23 完成 | 练习/错题本/画像/复习四个页面都能用 | 逐页排查 API 和渲染 |

> **Phase 1 完成后**：应用已是一个完整可用的 OJ + 错题本 + 画像 + 复习系统，只是 WA 时不带 AI 错因分析。此时可以开始学课程并进入 Phase 2。

---

### Phase UX：前端体验、多语言与题库预准备（切片 PU-0 ~ PU-17）

> 完整切片定义见 **`docs/PHASE-UX.md`**。分为五组：
> A 组启动体验（PU-0~3）、B 组网页端（PU-4~6）、C 组视觉改版（PU-7~10）、
> D 组题库系统（PU-11~14）、E 组多语言（PU-15~17）。
> 推荐顺序 **A → E → D → B → C**。
> 该阶段不改 `sidecar/app/agent/`、不接 LLM —— Agent 由用户手写。

---

### Phase 2：AI Agent 开发（切片 P2-0 ~ P2-22）

> 此时 Phase 1 已完成，课程应已学到对应章节。每个切片标注先学课节。

#### P2-0：LLM Gateway
- **产出**：`sidecar/app/llm/gateway.py`
- **内容**：统一 `chat_completion(messages, **kwargs) -> str` 函数，根据 config 切换 DeepSeek / Ollama，统一 OpenAI 兼容协议
- **验证**：pytest mock 测两个 provider 路由正确；真实调一次 DeepSeek API 返回非空
- **前置**：P1-2
- **先学课节**：C1:12（调用 DeepSeek）、C1:14（ChatOpenAI 兼容用法）、C1:16（init_chat_model）、C1:18（Ollama 本地模型调用）、C1:21（流式/批量/异步调用）

#### P2-1：Prompt 模板系统
- **产出**：`sidecar/app/prompts/error_analysis.md`、`sidecar/app/prompts/loader.py`
- **内容**：模板文件 + 加载器，支持变量插值（`{{code}}`、`{{expected_output}}` 等）
- **验证**：加载模板并插值后内容正确
- **前置**：无
- **先学课节**：C1:25-26（消息与消息格式）、C1:30-31（ChatPromptTemplate 实例化与参数类型）、C1:32（部分变量预填充与消息占位符）

#### P2-2：错因分析 JSON Schema
- **产出**：`sidecar/app/agent/schemas.py`
- **内容**：定义 `ErrorAnalysisResult` Pydantic model（error_type、error_detail、hint、suggested_fix）
- **验证**：合法 JSON 通过校验，非法 JSON 抛错
- **前置**：无
- **先学课节**：C1:41（结构化输出）、C1:42-44（Pydantic 基本使用与高级特性）、C1:46-49（TypedDict/JSON Schema/dataclass 与四种校验模式）、C2:14-15（状态 pydantic 实现）

#### P2-3：错因分析函数
- **产出**：`sidecar/app/agent/analyzer.py`
- **内容**：`analyze_error(code, stdin, expected, actual) -> ErrorAnalysisResult`，组装 prompt → 调 LLM Gateway → 解析 + Schema 校验 → 失败则重试一次
- **验证**：拿一段真实 WA 代码跑通，输出结构化标签
- **前置**：P2-0、P2-1、P2-2
- **先学课节**：C1:50（两种获取结构化结果的方式）、C1:51-54（Agent 概述与 invoke 调用）、C1:57（Agent 结构化输出策略）

#### P2-4：错因分析接入判题 API + 错题本回填
- **产出**：修改 `sidecar/app/api/judge.py`、修改 `sidecar/app/api/mistakes.py`
- **内容**：判题返回 WA 时自动触发 `analyze_error`，结果附加到判题响应 + 回填错题本 error_type 字段
- **验证**：curl 提交一段错误代码，返回 WA + 错因标签；错题本页面能看到错因
- **前置**：P1-14、P1-18、P2-3

#### P2-5：S2 验证集 ★ go/no-go
- **产出**：`tests/test_s2_accuracy.py`、`corpus/eval_cases/`（20 道真题）
- **内容**：20 道带人工标注错因的 WA 用例，批量跑 `analyze_error`，统计准确率
- **验证**：准确率 ≥ 60% → 过关进 P2-6；不达标 → 调 prompt / 换模型 / 提前进 P2-13 加 RAG
- **前置**：P2-3
- **先学课节**：C1:23-24（LangSmith 介绍与使用，用于调试和追踪分析结果）

#### P2-6：工具函数定义
- **产出**：`sidecar/app/agent/tools.py`
- **内容**：定义 `run_code`、`judge`、`search_knowledge`（占位）三个工具函数的 schema 和实现
- **验证**：pytest 调每个工具返回正确
- **前置**：P1-11、P1-12、P1-13
- **先学课节**：C1:33（工具调用方式与执行流程）、C1:34（不使用 @tool 定义工具）、C1:35-37（@tool 装饰器：description/name/args_schema/docstring）

#### P2-7：Function Calling 集成
- **产出**：`sidecar/app/agent/tool_caller.py`
- **内容**：调 LLM 时传入 tools schema，解析模型返回的 function_call，执行对应工具，结果回传
- **验证**：给模型一个需要跑代码才能回答的问题，验证它主动调 `run_code`
- **前置**：P2-0、P2-6
- **先学课节**：C1:38-39（多工具调用与 tool_choice）、C1:40（工具使用实践经验）、C1:54（Agent 绑定工具并调用）、C2:41（大模型配置工具调用）、C2:101-103（工具节点与 ToolNode）

#### P2-8：前端显示 Agent 工具调用
- **产出**：扩展 `OutputPanel.vue`
- **内容**：展示 Agent 调了什么工具、工具返回什么
- **验证**：前端能看到 Agent 的工具调用过程
- **前置**：P1-16、P2-7

#### P2-9：Agent 状态定义
- **产出**：`sidecar/app/agent/state.py`
- **内容**：定义 `AgentState` TypedDict（code、stdin、expected、actual、retrieved_knowledge、analysis、is_valid、retry_count）
- **验证**：类型检查通过
- **前置**：P2-2
- **先学课节**：C2:09（LangGraph 三要素组成）、C2:14-15（状态 dataclass/pydantic 实现）、C2:16-18（reducer 合并与归约）、C2:22-27（四种状态与预定义状态 AgentState）

#### P2-10：Agent 节点实现
- **产出**：`sidecar/app/agent/nodes.py`
- **内容**：`read_code_node`、`retrieve_node`（占位）、`analyze_node`、`validate_node` 四个节点函数
- **验证**：每个节点单独 pytest 验证输入输出
- **前置**：P2-3、P2-9
- **先学课节**：C2:19-20（节点中调用与更新状态）、C2:21（节点并行执行）、C2:08（LangGraph 与 LangChain 定位区别）

#### P2-11：LangGraph 图组装
- **产出**：`sidecar/app/agent/graph.py`
- **内容**：用 `StateGraph` 组装节点，定义边和条件分支（validate 通过→END，不通过→retrieve，retry_count 超 3→END）
- **验证**：跑通一次完整流程，状态流转正确
- **前置**：P2-10
- **先学课节**：C2:10（图运行过程）、C2:12（完成基础 graph 流程）、C2:28-29（边与 add_sequence）、C2:32-33（条件分支与 path_map）、C2:36（动态分支）、C2:40-43（循环结构与动态实现）、C2:44（限制循环步数）、C2:45-46（主动/被动退出）

#### P2-12：图替换原单次调用
- **产出**：修改 `sidecar/app/agent/analyzer.py` 和 `sidecar/app/api/judge.py`
- **内容**：WA 触发时改为调 LangGraph 图而非单次 LLM
- **验证**：端到端测试，WA 时走图流程，输出与 P2-4 一致或更好
- **前置**：P2-4、P2-11
- **先学课节**：C1:55（工具调用流程分析）、C1:56（设置 agent name 和 system_prompt）、C1:63（Agent 实战）、C2:100（大模型 agent 部署）

#### P2-13：Milvus 初始化
- **产出**：`sidecar/app/rag/vector_store.py`
- **内容**：Docker 启动 Milvus，初始化连接，创建 Collection
- **验证**：能插入和检索向量
- **前置**：P1-10
- **先学课节**：C1:113-114（Milvus 安装与数据模型）、C1:115-116（Milvus DDL/DML/DQL 操作）

#### P2-14：Embedding 模型
- **产出**：`sidecar/app/rag/embedding.py`
- **内容**：用 LangChain Embedding 接口生成文本嵌入向量
- **验证**：同义文本嵌入向量距离近，不同文本距离远
- **前置**：P2-13
- **先学课节**：C1:112（嵌入模型初始化及文档向量化）

#### P2-15：知识库语料构建
- **产出**：`corpus/knowledge_cards/` 目录，若干 Markdown 知识点卡片
- **内容**：动态规划、二分、贪心、图论等知识点的算法卡片
- **验证**：每个卡片有标题、知识点标签、正文
- **前置**：无
- **先学课节**：C1:104-105（TextLoader/CSVLoader/JSONLoader 加载文件）、C1:106-107（PyPDFLoader 及其它加载器）、C1:108-111（切分策略与各种 TextSplitter）

#### P2-16：语料索引脚本
- **产出**：`sidecar/app/rag/indexer.py`
- **内容**：读取 corpus 目录，生成嵌入，批量写入 Milvus
- **验证**：运行脚本后 Milvus 有数据
- **前置**：P2-14、P2-15
- **先学课节**：C1:117-118（文档切分向量化并写入 Milvus）

#### P2-17：检索节点接入图
- **产出**：修改 `sidecar/app/agent/nodes.py` 的 `retrieve_node`
- **内容**：用 Agent 当前上下文检索知识库 Top-3，组装进 AgentState
- **验证**：检索结果与题目知识点相关（人工检查几道）
- **前置**：P2-11、P2-16
- **先学课节**：C1:119-120（初始化 Agent 检索函数、生成回答）、C2:104-106（ToolRuntime 自定义工具与 wrap_tool_call）

#### P2-18：自检节点
- **产出**：扩展 `sidecar/app/agent/nodes.py`
- **内容**：`validate_node` 不再是简单 Schema 校验，而是调 LLM 自检分析结果是否合理（给一个自检 prompt）
- **验证**：给一个明显错误的分析结果，自检节点返回 `is_valid=False`
- **前置**：P2-11
- **先学课节**：C2:40-43（循环结构与动态实现）、C2:44（限制循环步数）、C2:48-49（重试机制与 retry_on）、C2:50-51（超时控制与错误处理）

#### P2-19：循环收敛测试
- **产出**：扩展 `tests/test_agent_graph.py`
- **内容**：10 道题测循环是否在 3 次内收敛，统计收敛率
- **验证**：收敛率 ≥ 80% → 过关；不达标 → 调自检 prompt 或降低重试上限
- **前置**：P2-18
- **先学课节**：C2:44（限制循环步数）、C2:45-46（主动/被动退出）、C2:98-99（LangSmith 调试使用）

#### P2-20：Guardrails 兜底
- **产出**：`sidecar/app/agent/guardrails.py`
- **内容**：LangChain 1.x 中间件护栏（PIIMiddleware 内容过滤 + ModelFallback 降级 + ModelRetry 重试 + ToolCallLimit 限制）
- **验证**：模拟 LLM 返回非法 JSON，中间件拦截并重试或降级
- **前置**：P2-12
- **先学课节**：C1:64-66（中间件分类与 SummarizationMiddleware/HumanInTheLoop）、C1:67-68（PIIMiddleware/TodoListMiddleware）、C1:69-72（ModelCallLimit/ToolCallLimit/ModelFallback/LLMToolSelector）、C1:73-74（ToolRetry/ModelRetry）、C1:78（多个中间件组合及执行顺序）

#### P2-21：错题本错因筛选启用
- **产出**：修改 `desktop/src/renderer/src/views/MistakesView.vue`
- **内容**：启用 P1-21 中预留的错因标签筛选（error_type 现在有 AI 填充的数据了）
- **验证**：错题本页面按错因筛选有效
- **前置**：P2-4、P1-21

#### P2-22：端到端集成测试
- **产出**：`tests/test_e2e.py`
- **内容**：完整流程测试：写代码→运行→WA→Agent 分析→错因入库→错题本可见→画像更新→复习调度
- **验证**：全链路走通，无报错
- **前置**：P2-12、P2-17、P2-19、P2-20、P2-21

---

### Phase 2 完成检查点

| 检查点 | 时机 | 检查内容 | 不通过怎么办 |
|---|---|---|---|
| **P2-5 准确率** | **P2-5 完成** | **20 道真题准确率 ≥ 60%** | **调 prompt / 换模型 / 提前进 P2-13 加 RAG** |
| P2-12 图跑通 | P2-12 完成 | LangGraph 图端到端跑通 | 检查状态传递、条件分支逻辑 |
| P2-17 检索相关性 | P2-17 完成 | 检索结果人工检查相关 | 调嵌入模型 / 检查语料质量 |
| P2-19 收敛率 | P2-19 完成 | 自检循环 3 次内收敛率 ≥ 80% | 调自检 prompt / 降重试上限 |
| P2-22 全链路 | P2-22 完成 | 端到端无报错 | 逐环节排查 |

---

### Phase 3：扩展阶段（按需）

#### S7 Multi-agent
- **内容**：错因/提示/变式题三 Agent 协作
- **先学课节**：C2:114-127（子图：父图调用子图、作为节点、持久化策略、动态路由）、C2:128-132（图设计模式：提示词链/路由/编排器-工作者/评估器-优化器/agent 模式）

#### S8 Memory + Streaming
- **内容**：短期/长期记忆 + 前端流式渲染
- **先学课节**：C1:86-88（短期记忆 InMemory 持久化）、C1:89-93（PostgreSQL 持久化与对比）、C1:94-96（消息裁剪/删除/摘要治理 + 长期记忆架构）、C1:97-101（put/get/search + 工具/中间件中访问长期记忆）、C2:56-83（持久化：检查点/内存/PostgreSQL/fork/长期记忆/环境上下文记忆）、C2:107-113（流式执行：状态/messages/checkpoints/自定义/astream_events）、C1:62（Agent 流式输出策略）

#### S9 Eval + Observability
- **内容**：评估集 + LangSmith 链路追踪
- **先学课节**：C1:23-24（LangSmith 介绍与主要功能）、C2:98-99（LangSmith 对接与调试）

#### S10 打包
- **内容**：PyInstaller 打包 sidecar + electron-builder 出安装包
- **前置**：P2-22 完成

---

## 三、Vibecoding 提示词模板

### 开发新功能时

```
我在做 AlgoForge 项目的【Phase {阶段号}】阶段，当前切片是【P{阶段号}-{N}：{名称}】。

请先读以下文件了解约束和现有代码：
1. AGENTS.md（技术红线）
2. {相关现有文件路径}

任务：{具体描述，包含函数签名、输入输出、边界条件}

约束：
- 只创建/修改以下文件：{明确列出文件路径}
- 不要动其他文件
- 不要重构无关代码
- {语言}命名规范见 AGENTS.md 第四节
- 写完后补一个 pytest/vitest 测试
```

### 调试问题时

```
【P{阶段号}-{N}】出问题了。

现象：{实际发生了什么}
预期：{应该发生什么}
相关代码：{贴文件路径或代码片段}
错误信息：{完整报错}

请先读 AGENTS.md 确认没有违反技术约束，然后定位问题根因，只修这一处，不顺手改别的。
```

### Code Review 时

```
请读以下文件，对照 AGENTS.md 检查：
1. 是否违反技术栈锁定
2. 是否违反工程红线
3. 命名是否规范
4. 是否有未处理的边界

文件：{贴路径}
只报告问题，不要改代码。
```

## 四、上下文管理建议

### AI 上下文会爆时

长对话中 AI 会遗忘早期约束。应对策略：
1. **每个切片开新对话**：一个切片一个 session，不让旧上下文干扰
2. **开头先贴 AGENTS.md 核心约束**：技术栈锁定表 + 工程红线
3. **贴当前切片相关的接口定义**：如 Pydantic model、TS interface
4. **不贴无关大文件**：如 node_modules、生成的迁移文件

### 切片间衔接

新切片开始时，给 AI 的第一句话：
```
上一个切片【P{阶段号}-{N-1}】已完成，{验证方式}通过。
现在做切片 P{阶段号}-{N}。上一切片产出文件在 {路径}，接口定义如下：{贴签名}
```

## 五、课节速查表

| 阶段 | 课程一（C1） | 课程二（C2） | 时机 |
|---|---|---|---|
| Phase 1（纯工程） | 07-08（环境） | 02-07（环境） | Phase 1 期间并行学，不阻塞 |
| P2-0 LLM 调用 | 11-22, 25-32 | — | 动手 P2-0 前学完 |
| P2-2 结构化输出 | 41-50 | 14-15 | 动手 P2-2 前学完 |
| P2-3 Agent 基础 | 51-57 | — | 动手 P2-3 前学完 |
| P2-5 调试追踪 | 23-24 | — | 动手 P2-5 前学完 |
| P2-6~7 工具调用 | 33-40, 54 | 41, 101-103 | 动手 P2-6 前学完 |
| P2-9~11 状态图 | — | 08-47 | 动手 P2-9 前学完 |
| P2-13~17 RAG | 104-120 | — | 动手 P2-13 前学完 |
| P2-18~19 循环重试 | — | 40-51 | 动手 P2-18 前学完 |
| P2-20 护栏 | 64-78 | — | 动手 P2-20 前学完 |
| S7 多 Agent | — | 114-132 | S7 时学 |
| S8 记忆+流式 | 62, 86-101 | 56-83, 107-113 | S8 时学 |
| S9 可观测 | 23-24 | 98-99 | S9 时学 |

## 六、一句话原则

> **每个切片小到 AI 一次能写完、你能一眼验证、十分钟内能 commit。Phase 1 不需要课程知识，可以边做边学。Phase 2 先学对应课节再动手。**
