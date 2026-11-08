# AlgoForge

**基于智能体的编程课程学习过程分析与反馈系统**

面向编程类课程的教学辅助系统 —— 过程可溯、学情可见、反馈分层。

[![CI](https://github.com/xtzwww/AlgoForge/actions/workflows/ci.yml/badge.svg)](https://github.com/xtzwww/AlgoForge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vue.js&logoColor=white)
![Electron](https://img.shields.io/badge/Electron-33+-47848F?logo=electron&logoColor=white)

---

## 这个项目解决什么问题

线上编程教学普及后，两个痛点一直没被解决：

1. **学情不可见** —— 教师只看到最终提交结果，看不到学生「怎么学、卡在哪」。现有平台的学情分析大多停留在登录时长、视频观看这类浅层行为指标。
2. **反馈无效** —— 传统 OJ 只给 AC / WA 判定，不给「为什么错、怎么想」。学生拿到 WA 之后，纠错成本全部压在自己身上，同样的错误反复犯。

AlgoForge 同时应对这两点：**让学习过程可观测，让反馈可分层、可追溯。**

整个系统以「反馈」组织，而不是把功能平铺成「学生端 + 教师端 + 检测」三块：

```
                      学习过程数据
          （提交结果 + 编辑行为轨迹 + 历史记录）
                            │
            ┌───────────────┴───────────────┐
            ▼                               ▼
     给学生 · 学反馈                   给教师 · 教反馈
   分层释放的错因引导                班级知识点掌握度
   「这题你卡在哪」                  「全班弱在哪」
   「这个错误你犯过 N 次」            「下节课该讲什么」
```

---

## 核心特性

### 一、分层释放的错因引导

AI 诊断出三段内容（`错在哪` / `提示` / `怎么改`），但**不一次性给学生**：

```
提交（WA）
   ↓
【第一层】错在哪      指出实际错因，用学生能读的中文说明
   ↓  点「还是不会，给点提示」
【第二层】提示        给方向，仍不给答案
   ↓  点「还是不会，直接看怎么改」
【第三层】怎么改      完整的修改建议
   ↓  点「重新自己想」
（收起，回到只看第一层）
```

教学设计依据是**支架式教学（scaffolding）**——逐步撤除支持，让学生尽可能自主完成。三个刻意的取舍：

- **初始什么都不显示**，连「错在哪」也在按钮后面。默认展开会让「还是不会」退化成「显示剩余内容」。
- **释放层数按实际内容算**，不是固定三层。模型跳过某一节时按钮直接跳到下一节，不会点开一个空面板。
- **释放进度不持久化**，离开页面再回来重新从第一层开始——门槛是一次自我评估。

### 二、教师端：教学决策依据，不是管理工具

排课、考勤、通知、成绩核算不在范围内。教师端回答的是教学问题：

| 能力 | 回答的教学问题 |
|---|---|
| 班级知识点掌握度 | 我讲的学生掌握了吗？谁最弱？ |
| 班级错因分布 | 全班 60% 的 WA 都是边界条件 → 下节课讲这个 |
| 学生个人错因时间线 | 这个学生到底卡在哪？ |
| 提交审阅 | 代码 + 错因诊断 + 过程特征三块汇合 |
| 学情预警 | 哪些学生需要我关注？ |
| 出题与作业管理 | 我要留什么题、什么时候交？ |
| AI 辅助强度设定 | 这份作业允许 AI 帮到什么程度？ |

### 三、AI 辅助强度：五档，由老师定

学生做题时编辑器旁可以有 AI 助手，**帮到什么程度由发布作业的老师决定**。档位存在作业上，前端只传 `assignment_id`；越档请求在**调用模型之前**就被拒绝。

| 档 | 学生看到的名字 | 能力 |
|---|---|---|
| 一 | 本机补全 | 查本机词表补全，**不联网、不调用任何模型** |
| 二 | AI 补全 | 联网模型补全 |
| 三 | 答疑 | 可提问，**只给提示、不能写进代码** |
| 四 | 代写片段 | AI 可以写一段，学生决定是否采纳 |
| 五 | 完整解法 | 连整题解法都能给 |

能力随档位单调递增，不会出现「三档能做、四档反而不能」。三档额外有一道内容门：模型回复若看起来像代码，会被降级成散文提示。每次补全 / 提问 / 采纳都记一行 `assistance_usage`——**只记动作和次数，不记对话内容**。

### 四、AI 使用检测：过程证据提示，不是自动判定

按可信度降序三层，当前实现聚焦第一层：

| 层 | 依据 | 可信度 | 状态 |
|---|---|---|---|
| 第一层 | 编辑行为轨迹：粘贴事件、编辑时间线、粘贴后改写比例 | 最高（客观可复现） | 已实现（分类信号，无概率、无分数） |
| 第二层 | 历史风格一致性：命名、缩进、注释密度是否跃变 | 中（需 2–3 次基线） | 按需 |
| 第三层 | LLM 语义判断：是否超出该学生当前知识范围 | 兜底 | 按需 |

**绝不输出单一的「AI 概率 87%」。** 误判一个自己写代码的学生，比漏判一个用 AI 的学生严重得多——学生会失去信任，教师会弃用。教师可复核、可驳回；学生端只给提示，不给分数。

### 五、其余学生端能力

在线编程（Monaco + 多语言切换）、多语言判题（C++ / Python / Java，判 AC / WA / TLE / MLE / CE / RE）、错题本（按错因与知识点筛选、失败可重试）、12 个封闭错因标签的中文解释、跨题目的历史联动反馈、SM-2 间隔重复复习、知识点能力画像（ECharts 雷达图 + 成长曲线）。

---

## 系统架构

```
┌────────── 学生端 · Electron 桌面 ──────────┐   ┌────── 教师端 · Web 控制台 ──────┐
│  Renderer (Vue 3 + TypeScript)            │   │  同一 Vue 应用（web 构建目标）  │
│  ├─ Monaco Editor + 编辑行为埋点           │   │  ├─ 班级学情看板                │
│  ├─ 分层反馈面板（错因逐层释放）           │   │  ├─ 错因分布聚合                │
│  ├─ 能力画像（ECharts）                    │   │  ├─ 学生个人错因时间线          │
│  └─ axios + WebSocket                     │   │  └─ 提交审阅（含过程标记）      │
│  Main 进程：spawn & 守护 sidecar、工具链探测│   └───────────────┬────────────────┘
└────────────────┬──────────────────────────┘                   │
                 └──────────────────┬─────────────────────────────┘
                     localhost:随机端口 + Token 鉴权
┌──────────────────────────────────────────────────────────────────┐
│  Sidecar (Python / FastAPI)                                      │
│  ├─ REST API + WebSocket 事件流                                   │
│  ├─ 错因分析 Agent（LangGraph 编排 + 工具循环 + JSON Schema 强校验）│
│  ├─ RAG 检索（Milvus + 算法知识库语料）                            │
│  ├─ 过程特征提取（编辑轨迹 → 特征 → 反馈信号）                     │
│  ├─ 学情聚合（知识点正确率 / 错因分布，按 user 与 tag 两种切片）   │
│  ├─ 复习调度（SM-2）                                              │
│  ├─ Sandbox：subprocess + 超时 + 内存上限（多语言）                │
│  └─ PostgreSQL + SQLAlchemy 2.0 + Alembic                        │
└──────────────────────────────────────────────────────────────────┘
```

**架构决策要点**

- **双进程分离**：UI（Electron）与计算（Python sidecar）解耦，各用最擅长的技术栈。
- **一后端两前端**：学生端 Electron + 教师端 Web 共用同一 FastAPI，避免两套业务逻辑。
- **不引入消息队列**：不依赖 Redis / RabbitMQ / Celery，单机场景 asyncio 足够。
- **模型可插拔**：LLM Gateway 统一 OpenAI 兼容协议，换模型只改配置不改代码。
- **反馈分层在前端控制**：后端返回结构化三段内容，前端控制释放时机——「释放几层」因此是可调的教学策略，而不是写死在数据里。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 桌面壳（学生端） | Electron 33+ |
| Web 控制台（教师端） | 同一 Vue 应用 + Vite `web` 构建目标 |
| 前端框架 | Vue 3 `<script setup>` + TypeScript |
| UI 组件库 | Naive UI 2.45（配色由 `naive-theme.ts` 从三套 CSS 变量桥接） |
| 样式 | Tailwind CSS |
| 编辑器 | Monaco Editor |
| 可视化 | ECharts 6 + vue-echarts |
| 状态管理 | Pinia |
| 后端 | Python 3.11 + FastAPI + Uvicorn |
| 数据校验 | Pydantic v2（请求 / 响应 / LLM 输出三重校验） |
| Agent 编排 | LangGraph 1.x + LangChain 1.x |
| 长期记忆 | PostgreSQL Checkpoint / Store |
| 向量检索 | Milvus（pymilvus） |
| ORM / 迁移 | SQLAlchemy 2.0 (async) + Alembic |
| 数据库 | PostgreSQL |
| 判题沙箱 | subprocess + 超时 + 内存上限 |
| 教学模型 | 知识点正确率统计 + SM-2 间隔重复 |
| 构建 | electron-vite（vue-ts） |
| 质量 | ESLint + Prettier / Ruff / pytest / Vitest |

LLM 接口走 OpenAI 兼容协议，主路径 DeepSeek API，兜底 Ollama 本地模型，也可接入任意兼容端点（vLLM / LM Studio 等），**换模型只改 `.env`**。

---

## 目录结构

```
AlgoForge/
├─ desktop/                 # 学生端 Electron + 教师端 Web（同一 Vue 应用，两个构建目标）
│  └─ src/
│     ├─ main/              # Electron 主进程：spawn 并守护 sidecar、拉起 PostgreSQL
│     └─ renderer/          # Vue 前端：views / components / router / stores
├─ sidecar/                 # Python 后端
│  └─ app/
│     ├─ api/               # REST 路由 + WebSocket
│     ├─ agent/             # LangGraph 图、节点、状态、子 Agent 团队
│     ├─ rag/               # Milvus 向量检索、语料切分嵌入
│     ├─ prompts/           # Prompt 模板（版本化管理，不硬编码在代码里）
│     ├─ sandbox/           # 多语言判题执行器
│     └─ models/            # SQLAlchemy 模型
├─ corpus/                  # 算法知识库语料 + 错因评测用例
├─ tests/                   # pytest 测试套件
├─ docs/                    # 设计文档（详见下方“文档索引”）
└─ tools/                   # 语料生成、演示数据、评估脚本
```

---

## 快速开始

**前置要求**：Node.js ≥ 20、Python ≥ 3.11、PostgreSQL、g++ / clang（判 C++ 用；判 Java 另需 JDK，JRE 不够）

```bash
# 1. 后端配置
cd sidecar
cp .env.example .env
#    至少填这一项，指向本机编译器：
#    CPP_COMPILER_PATH=D:\path\to\ucrt64\bin\g++.exe
#    LLM 相关见 .env 内注释（provider 切换、token 上限、超时）

# 2. 后端依赖 + 建表
pip install -r requirements.txt
alembic upgrade head

# 3. 学生端（会自动拉起 sidecar 与 PostgreSQL）
cd ../desktop
npm install
npm run dev

# 4. 教师端（浏览器访问，共用同一后端）
npm run dev:web
```

> **数据库没起来的症状不是「没有持久化」，而是「每一页都 Network Error」。**
> `/health` 不碰数据库，会照常返回 200，主进程据此正常开窗；而题库、错题本、教师端
> 连不上 5432 会直接挂住，日志里没有任何异常。排查第一步永远是
> `netstat -ano | findstr 5432`，而不是去翻 sidecar 日志。

更完整的环境说明、打包版目标机要求、常见启动问题排查，见 [docs/PROJECT-DETAIL.md](docs/PROJECT-DETAIL.md)。

---

## 测试与质量

```bash
# 后端（不调用真实模型）
pytest

# 只跑需要真实模型的评测（会调用付费端点，按测试计费，结果不可复现）
pytest -m eval_set

# 前端
cd desktop && npm run test
npm run typecheck
npm run lint
```

后端约 1100 个 pytest 用例、前端 19 个 Vitest 测试文件，CI 跑 lint / typecheck / 双端测试。

---

## 开发路线图

项目按 S0–S10 推进，每个阶段有明确的「完成定义」和「准入条件」，前序阶段未完成不启动后续：

| 阶段 | 内容 | 状态 |
|---|---|---|
| S0 | 脚手架：Electron 窗口 + Monaco + sidecar `/health` | 完成 |
| S1 | 判题闭环：编译 → 运行 → 比对 → AC/WA/TLE，日志 WebSocket 流式推送 | 完成 |
| S2 | 单次 Agent 调用（**go/no-go 节点**：20 道真题错因准确率 ≥ 60%） | 完成 |
| S3 | Function Calling：Agent 自主调 `run_code()` / `judge()` | 完成 |
| S4 | LangGraph 编排：读代码 → 检索 → 分析 → 自检，含条件分支 | 完成 |
| S5 | RAG：Milvus 检索算法知识库，结果组装进 Agent 上下文 | 完成 |
| S6 | ReAct 反思：自检不准则回到检索 / 分析节点，最多 3 次 | 完成 |
| S7 | Multi-agent：错因 / 提示 / 变式题三 Agent 协作 | 进行中 |
| S8 | Memory + Streaming：短期检查点 + 长期 Store + 前端流式渲染 | 进行中 |
| S9 | Eval + Observability：评估集 + LangSmith 追踪 | 部分 |
| S10 | Guardrails + 收尾：中间件护栏、画像、错题本、SM-2、打包 | 部分 |

工程约束（技术栈锁定、工程红线、阶段准入条件）见 [AGENTS.md](AGENTS.md)——它比这份 README 更严格，贡献代码前请先读。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [AGENTS.md](AGENTS.md) | 项目宪法：技术栈锁定、工程红线、阶段准入条件 |
| [docs/PROJECT-DETAIL.md](docs/PROJECT-DETAIL.md) | 完整设计说明：功能细节、实现状态、路线图、环境变量、已知局限 |
| [docs/ui-design-spec-2026-09-25.md](docs/ui-design-spec-2026-09-25.md) | UI 设计规范与三套主题 |
| [docs/auth-routing-design.md](docs/auth-routing-design.md) | 鉴权与路由守卫设计 |
| [docs/problem-corpus-research.md](docs/problem-corpus-research.md) | 题库与错因标签体系调研 |
| [VIBECODING.md](VIBECODING.md) | 用 AI 辅助开发的切片方法（历史文档，方法可参考，阶段划分已过时） |

---

## 已知局限

- **单机部署**：sidecar 绑定 `127.0.0.1`，CORS 白名单很窄；教师端 Web 版本把 sidecar Token 编进前端产物，只适用于本机或可信内网的演示，**不能直接公网部署**。
- **AI 检测只做到第一层**（编辑行为轨迹），第二、三层是按需实现。
- **判题沙箱是 subprocess 级隔离**，不是容器级——够判 AC/WA/TLE，不承担对抗恶意代码的职责。
- **RAG 语料规模有限**（约 120 张知识卡片），覆盖面随课程内容增长。

---

## 许可

[MIT](LICENSE) © 2026 刘爱重
