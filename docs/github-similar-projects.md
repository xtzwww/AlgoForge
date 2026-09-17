# AlgoForge 同类项目调研

> 检索时间：2026-09-16 · 数据源：GitHub REST API v3（star 数为当日快照）
> 检索脚本与原始结果见文末「复现方式」

---

## 0. 结论先行

**没有任何一个开源项目同时具备 AlgoForge 的四环闭环，并做成桌面应用。**

AlgoForge 的四环是：`本地判题沙箱 → LLM 错因诊断 → 知识点画像 → SM-2 复习调度`。
最接近的 PrepDojo（31★）覆盖了前两环（本地沙箱判题 + AI 教练通过 Function Calling 调沙箱），
但没有 LangGraph 多步状态图、没有 RAG 知识库、没有复习调度与能力画像。

关于你提到的 `awesome-leetcode`：GitHub 上有两个同名方向的仓库
（[ashishps1/awesome-leetcode-resources](https://github.com/ashishps1/awesome-leetcode-resources) 17835★、
[hicodebear/awesome-leetcode-algorithm](https://github.com/hicodebear/awesome-leetcode-algorithm) 208★），
它们属于**内容汇总仓库**（题解、资料、路线图），不是**可运行系统**，与 AlgoForge 不在同一层。
但它们的价值在别处：**是 AlgoForge `corpus/` 现成的 RAG 语料来源**。

所以「类似项目」必须拆成四个圈层来谈，否则会拿题库去比系统。

---

## 1. 四圈层模型

| 圈层 | 类比 AlgoForge 的哪个模块 | 该层是否有直接竞品 | 对本项目的意义 |
|---|---|---|---|
| **A. 内容语料** | `corpus/`（题库 + 知识点卡片） | 极度饱和，头部 10 万★级 | 直接取材，不必自建 |
| **B. 判题内核** | `sidecar/app/sandbox/` | 成熟（Judge0 / go-judge / Hydro） | 抄判定语义与资源限制策略 |
| **C. AI 助教 / 错因诊断** | `sidecar/app/agent/` + `prompts/` | 有零星项目，无成熟产品 | **AlgoForge 的核心差异化所在** |
| **D. 学习调度 / 错题本** | `models/`（SM-2、画像） | 有多个同类，但多为通用学科 | 可对标，SM-2 已被 FSRS 迭代 |

---

## 2. 最近邻 Top 6（按与 AlgoForge 的重合度排序）

### ① yudongfang-thu/PrepDojo · 31★ · Python
`https://github.com/yudongfang-thu/PrepDojo`

**这是全网最接近 AlgoForge 的项目。** 本地优先的 AI 刷题工具。

| 维度 | PrepDojo | AlgoForge |
|---|---|---|
| 判题沙箱 | subprocess + POSIX rlimit + wall-clock 超时 | subprocess + 超时 + 内存限制 |
| 判定态 | AC/WA/TLE/MLE/RE + CE | AC/WA/TLE/MLE/CE/RE |
| AI 能力 | **AI 教练可自主调用判题沙箱**（`run_code` / `run_problem_case`） | Function Calling 调判题器/执行代码 |
| LLM 接入 | DeepSeek OpenAI 兼容 | LLM Gateway（DeepSeek 主 / Ollama 兜底） |
| 多步编排 | ❌ 单轮工具循环 | ✅ LangGraph 状态图 + 条件分支 |
| RAG | ❌ | ✅ Milvus + LangChain Embedding |
| 复习调度 | ❌（仅"排除最近 3 天练过的卡"） | ✅ SM-2 排期 |
| 能力画像 | ❌ | ✅ 知识点正确率 + ECharts 雷达图 |
| 形态 | 本地 Web UI（FastAPI + 无构建前端） | Electron 桌面 + Python sidecar |
| 数据 | SQLite | PostgreSQL |
| 语言 | Python 3 / C++17 | C++ |

**可直接借鉴**：它的「AI 教练 = 沙箱工具循环」设计写得非常克制且诚实——
`AI 看到的是沙箱跑出的真实结果——对错来自事实而非模型猜测`。
这句话正好是 AlgoForge S3（Function Calling）应该写进 prompt 的设计原则。
另外它的多用户部署手册（Docker 判题 + 配额 + HTTPS）对 S10 收尾有参考价值。

**它的短板就是 AlgoForge 的机会**：没有编排、没有检索、没有调度。三者恰好是 S4/S5/S10 的内容。

---

### ② huaxx-lab/LeetLens · 14★ · Swift 6（macOS 原生）
`https://github.com/huaxx-lab/LeetLens`

面向算法学习的力扣刷题客户端，**AI 从提问/对话/提交记录里推断掌握度 → 知识脑图 → FSRS 排期**。

- 亮点：它解决了一个 AlgoForge 还没正面处理的问题——**"模型讲得好 ≠ 你学会了"**。
  它只把「提问与对话中的新证据」「历史提交的交叉判断」作为掌握度依据，而不是让模型自评。
- 它用 **FSRS**（现代间隔重复算法）而非 SM-2，并用"遗忘曲线折算现在还记得多少"显示百分比。
- 判题**走 LeetCode 官方**，不是本地沙箱；内置浏览器登录 leetcode.cn。
- 可选服务（Redis 热缓存 / PostgreSQL+pgvector 跨对话检索 / JDT LS 补全）都只监听 127.0.0.1，
  挂了自动退回本机——这套「降级不中断」的思路值得 AlgoForge 的 RAG 层照抄。

**对 AlgoForge 的启示**：`error_cause` 这类字段不该由模型自评可信度，
而应由「多次提交的行为证据」累积。这与 AlgoForge 现有 `agent_error_type` 字段形成对照。

---

### ③ lciop123/recall-ai-mistake-notebook · 2★ · Python + Vue 3
`https://github.com/lciop123/recall-ai-mistake-notebook`

**AI 智能错题本**，技术栈与 AlgoForge 高度重合：FastAPI + Pydantic + Vue 3 + TS + Pinia + Tailwind。
功能对位：录入 → 归类（知识点/错因）→ SM-2 复习队列 → 原题重做 → **变式题** → 学习看板 → 周报。

- **错因采用受控词表**：`概念不清 / 审题失误 / 粗心 / 计算错误 / 方法不当 / 超纲 / 其他`，
  理由写得很清楚——「避免统计被同义表达拆散」。这正对应 AlgoForge 的错因 taxonomy 问题
  （P2-5 实测 Ollama 准确率 50%，修 taxonomy 前 35%）。
- 变式题（「举一反三」）是 AlgoForge S7 变式题 Agent 的现成产品化参考。
- 用 ChromaDB 做相似题检索，**向量环境不可用时自动退化为关键词匹配**——降级设计再次出现。

**差异**：面向通用学科（数学/物理），不做代码判题。所以它是"错题本"这一环的完整答案，
但不是"算法竞赛"的答案。

---

### ④ Qurse123/Ankicode · 3★ · Rust + React（Tauri 2）
`https://github.com/Qurse123/Ankicode`

Anki 式间隔重复的 LeetCode 桌面应用。**桌面 + 本地 SQLite + FSRS + Chromium 扩展**。

- 桌面架构值得对比：Tauri（Rust + SQLite + `127.0.0.1:17342` loopback API）vs AlgoForge 的 Electron + Python sidecar。
  两者都是"桌面壳 + 本地 HTTP 后端 + 随机端口"，但 Tauri 不需要 spawn 一个 Python 进程树。
- 它**没有本地判题**，也**没有 AI 分析**——只在 LeetCode 上做题，然后回来打分。
- 一个细节：Hard 题被追踪但不排期（因为不适合间隔重复）。这种「产品上的克制」值得借鉴。

---

### ⑤ karanb192/algo-sensei · 274★ · Prompt/Skill 包
`https://github.com/karanb192/algo-sensei`

Claude Code skill，五模式：Tutor / **Hint（5 级渐进提示）** / Review / Interview / Pattern Mapper。

- **它没有代码，全是 prompt 设计**。但 5 级渐进提示的梯度设计
  （观察线索 → 模式识别 → 方向 → 具体技巧 → 伪代码骨架）是 AlgoForge
  「苏格拉底式提示」最成熟的公开参考。
- 它的 Roadmap 里明确列着 **`[ ] Spaced repetition integration`** 和
  **`[ ] Progress tracking across sessions`** 尚未实现——从侧面印证了
  AlgoForge「诊断 + 追踪 + 调度」的闭环定位确实是一个空白。

---

### ⑥ MajeedKazemi/code-aid · 40★ · 有论文（arXiv 2401.11314）
`https://github.com/MajeedKazemi/code-aid`

CodeAid 是学术界被引用较多的 LLM 编程助教（课堂部署研究）。
核心结论：**学生需要的是概念解释与调试引导，而不是直接生成完整解答**。
AlgoForge 的 prompt 设计（不给答案、给错因标签 + 提示）与此一致，
可以把这篇论文作为 S2 方案设计的引用依据。

---

## 3. 各圈层清单

### A. 内容语料（→ `corpus/` 的取材来源）

| 仓库 | ★ | 语言 | 说明 |
|---|---|---|---|
| [TheAlgorithms/Python](https://github.com/TheAlgorithms/Python) | 224620 | Python | 算法实现大全，可直接切分为知识点卡片 |
| [CyC2018/CS-Notes](https://github.com/CyC2018/CS-Notes) | 186107 | — | 技术面试知识体系 |
| [labuladong/fucking-algorithm](https://github.com/labuladong/fucking-algorithm) | 135904 | Markdown | **算法套路框架**，最贴合"知识点"粒度 |
| [krahets/hello-algo](https://github.com/krahets/hello-algo) | 130134 | Java | 动画图解 DSA 教程，含 C++ 实现 |
| [MisterBooo/LeetCodeAnimation](https://github.com/MisterBooo/LeetCodeAnimation) | 76702 | Java | 动画题解 |
| [youngyangyang04/leetcode-master](https://github.com/youngyangyang04/leetcode-master) | 62493 | — | 《代码随想录》，60 万字详解 + 思维导图 |
| [azl397985856/leetcode](https://github.com/azl397985856/leetcode) | 55744 | JS | 题解记录 |
| [halfrost/LeetCode-Go](https://github.com/halfrost/LeetCode-Go) | 33807 | Go | 100% 测试覆盖的题解 |
| [ashishps1/awesome-leetcode-resources](https://github.com/ashishps1/awesome-leetcode-resources) | 17835 | Java | 你提到的 awesome-leetcode 类 |
| [greyireland/algorithm-pattern](https://github.com/greyireland/algorithm-pattern) | 15460 | Go | **算法模板**，适合做 prompt 的知识卡片 |
| [seanprashad/leetcode-patterns](https://github.com/seanprashad/leetcode-patterns) | 13747 | TS | 按模式（pattern）组织的题单 |
| [EndlessCheng/codeforces-go](https://github.com/EndlessCheng/codeforces-go) | 8714 | Go | 算法竞赛模板库（灵茶山艾府），竞赛向 |
| [afatcoder/LeetcodeTop](https://github.com/afatcoder/LeetcodeTop) | 20042 | — | 大厂高频题汇总 |
| [hicodebear/awesome-leetcode-algorithm](https://github.com/hicodebear/awesome-leetcode-algorithm) | 208 | — | 你提到的同名仓库 |

> 注意：这些都是**第三方内容**，直接入库有版权风险。
> PrepDojo 的做法值得抄——`仓库只含代码与自写种子内容`，`data/` 目录 `.gitignore` 排除，
> 不爬取、不内置题面，用户自带（BYO）。AlgoForge 的 `corpus/` 目前是自写的 40 个 yaml，这个边界要守住。

### B. 判题内核（→ 对标 `sandbox/` 的判定语义与资源限制）

| 仓库 | ★ | 语言 | 说明 |
|---|---|---|---|
| [QingdaoU/OnlineJudge](https://github.com/QingdaoU/OnlineJudge) | 6541 | Python | 配套 [QingdaoU/Judger](https://github.com/QingdaoU/Judger) 497★ —— **基于 seccomp 的判题沙箱**，C 实现，资源限制策略最值得读 |
| [hydro-dev/Hydro](https://github.com/hydro-dev/Hydro) | 6981 | TypeScript | 新一代 OJ 平台（vj5） |
| [judge0/judge0](https://github.com/judge0/judge0) | 4435 | — | 最通用的沙箱化代码执行系统，描述里已写 "for humans and AI" |
| [zhblue/hustoj](https://github.com/zhblue/hustoj) | 3794 | PHP | 老牌 OJ，ACM/NOIP 训练 |
| [DMOJ/online-judge](https://github.com/DMOJ/online-judge) | 1188 | Python | 配套 [judge-server](https://github.com/DMOJ/judge-server) 977★ —— **Python 判题后端**，与 sidecar 同语言，最易读 |
| [ZsgsDesign/NOJ](https://github.com/ZsgsDesign/NOJ) | 943 | PHP | 南邮开源 OJ |
| [criyle/go-judge](https://github.com/criyle/go-judge) | 618 | Go | Linux 容器技术沙箱，REST + gRPC |
| [vijos/vj4](https://github.com/vijos/vj4) | 545 | Python | 2005 年至今的老 OJ |
| [vfleaking/uoj](https://github.com/vfleaking/uoj) | 554 | JS | Universal OJ |

**只读这 2 个就够**：`QingdaoU/Judger`（seccomp 资源限制的工业实现）和 `DMOJ/judge-server`（Python 判题流程）。
AlgoForge 的 AGENTS.md 明确「不自研 setrlimit」，所以这里的目标是**对照判定语义**（TLE/MLE/RE 的边界条件怎么定），不是照搬实现。

### C. AI 助教 / 错因诊断（→ AlgoForge 的核心战场）

| 仓库 | ★ | 语言 | 与 AlgoForge 的关系 |
|---|---|---|---|
| [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) | 39741 | Python | 终身个性化辅导，多 agent 系统。**方向相同但目标是通用学科** |
| [JushBJJ/Mr.-Ranedeer-AI-Tutor](https://github.com/JushBJJ/Mr.-Ranedeer-AI-Tutor) | 29579 | — | 纯 prompt 的个性化教学模板，分层教学法参考 |
| [karanb192/algo-sensei](https://github.com/karanb192/algo-sensei) | 274 | — | 见 Top 6 ⑤ |
| [HugeCatLab/ChatTutor](https://github.com/HugeCatLab/ChatTutor) | 1315 | Vue | 可视化交互 AI 导师 |
| [MajeedKazemi/code-aid](https://github.com/MajeedKazemi/code-aid) | 40 | — | 见 Top 6 ⑥，有论文 |
| [towardsai/ai-tutor-app](https://github.com/towardsai/ai-tutor-app) | 29 | Python | **LangGraph + FastAPI + RAG 的助教**，技术栈与 AlgoForge 最接近的参考实现 |
| [HumphreySun98/Smart-Study-Agent](https://github.com/HumphreySun98/Smart-Study-Agent) | 56 | JS | RL 选动作 + FSRS 定时间 + LLM 只负责写内容——**职责分离的架构值得学** |
| [XiaoCow666/CodeSense](https://github.com/XiaoCow666/CodeSense) | 4 | Python | AI 辅助编程教育平台：代码评估 + 引导式学习 |
| [xiaohuoji2011/OI-Agent](https://github.com/xiaohuoji2011/OI-Agent) | 0 | Python | **AI 信息学竞赛智能体平台**，让大模型以选手身份参加 OI/NOI/CSP 并统一评测 |
| [ZvezdyUTO/XCPC-Training-Agent](https://github.com/ZvezdyUTO/XCPC-Training-Agent) | 7 | Go | LLM agent 系统做 ACM/ICPC 训练 |
| [rezabrizi/LitCoach](https://github.com/rezabrizi/LitCoach) | 18 | JS | 针对 LeetCode 的反馈 Chrome 扩展（有论文） |
| [adriangitvitz/leetcodetutor](https://github.com/adriangitvitz/leetcodetutor) | 2 | Python | 双栏 LeetCode 导师，多 persona 模式 |
| [BytePhilosopher/CP-FOCUS](https://github.com/BytePhilosopher/CP-FOCUS) | 18 | HTML | **反方向需求**：在 Codeforces/LeetCode 上屏蔽 AI 聊天，只给渐进提示 |

> **CP-FOCUS 是个有趣的信号**：已经有人在做"阻止学生用 AI 抄答案"的工具了。
> 这说明 AlgoForge「给错因和提示、不给解答」的产品判断是对的，且这个市场痛点真实存在。

### D. 学习调度 / 错题本（→ 对标 `models/` 的 SM-2 与画像）

| 仓库 | ★ | 语言 | 说明 |
|---|---|---|---|
| [ankitects/anki](https://github.com/ankitects/anki) | 30602 | Rust | 间隔重复的事实标准 |
| [open-spaced-repetition/fsrs4anki](https://github.com/open-spaced-repetition/fsrs4anki) | 4067 | — | **FSRS：SM-2 的现代替代**，AlgoForge S10 若想升级调度算法看这里 |
| [open-spaced-repetition/py-fsrs](https://github.com/open-spaced-repetition/py-fsrs) | 488 | Python | FSRS 的 Python 实现，可直接替换 SM-2 |
| [olmps/memo](https://github.com/olmps/memo) | 1869 | Dart | **面向编程的 SRS**（spaced repetition software），与 AlgoForge 的错题本最对位 |
| [wttwins/wrong-notebook](https://github.com/wttwins/wrong-notebook) | 718 | TS | 基于 AI 的学生错题本 |
| [Bairong-Xdynamics/MistakeNotebookLearning](https://github.com/Bairong-Xdynamics/MistakeNotebookLearning) | 127 | Python | ACL 2026 Findings 论文：错题本学习，有方法论 |
| [Qurse123/Ankicode](https://github.com/Qurse123/Ankicode) | 3 | Rust | 见 Top 6 ④ |
| [lciop123/recall-ai-mistake-notebook](https://github.com/lciop123/recall-ai-mistake-notebook) | 2 | Python | 见 Top 6 ③ |
| [xuyuandong/MistakeNotebook](https://github.com/xuyuandong/MistakeNotebook) | 5 | TS | AI 驱动的学生错题管理与复习系统 |
| [DuckInAShirt/leetmate](https://github.com/DuckInAShirt/leetmate) | 9 | Go | 终端 LeetCode 教练：提示而非答案 + 间隔复习 + ACM 模式 |
| [kudzaiprichard/anvil](https://github.com/kudzaiprichard/anvil) | 6 | Rust | 离线优先的编程面试/DSA 练习桌面应用 |

### E. 工程参考（→ LangGraph / RAG / 桌面架构）

| 仓库 | ★ | 语言 | 用途 |
|---|---|---|---|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 41718 | Python | S4 状态图的官方实现与示例 |
| [langchain-ai/deepagents](https://github.com/langchain-ai/deepagents) | 29455 | Python | LangChain 官方 agent harness |
| [JoshuaC215/agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit) | 4489 | Python | **LangGraph + FastAPI 服务化**的完整工具包，与 sidecar 架构同构 |
| [langchain-ai/agent-chat-ui](https://github.com/langchain-ai/agent-chat-ui) | 3156 | TS | LangGraph 前端的流式渲染参考（S8） |
| [wassim249/fastapi-langgraph-agent-production-ready-template](https://github.com/wassim249/fastapi-langgraph-agent-production-ready-template) | 2659 | Python | FastAPI + LangGraph 生产级模板 |
| [FareedKhan-dev/all-agentic-architectures](https://github.com/FareedKhan-dev/all-agentic-architectures) | 4508 | — | 35 种 agent 架构（含 Reflexion）—— S6 ReAct 自检循环的参考 |
| [NirDiamant/GenAI_Agents](https://github.com/NirDiamant/GenAI_Agents) | 24297 | — | 50+ agent 技术教程 |
| [LeetCode-OpenSource/vscode-leetcode](https://github.com/LeetCode-OpenSource/vscode-leetcode) | 8500 | TS | 桌面编辑器内做题的成熟形态 |
| [shuzijun/leetcode-editor](https://github.com/shuzijun/leetcode-editor) | 3982 | Java | IntelliJ 插件版 |
| [jinzcdev/leetcode-mcp-server](https://github.com/jinzcdev/leetcode-mcp-server) | 146 | TS | LeetCode MCP server，若想让 Agent 直接抓题可用 |
| [SPerekrestova/interactive-leetcode-mcp](https://github.com/SPerekrestova/interactive-leetcode-mcp) | 12 | TS | 引导式 DSA 学习的 MCP server |
| [algorithm-visualizer/algorithm-visualizer](https://github.com/algorithm-visualizer/algorithm-visualizer) | 48731 | JS | 算法可视化（AlgoForge 用 ECharts 画像，不是同一用途） |

---

## 4. 空白地带：AlgoForge 的真实位置

把上面的清单按「是否具备四个能力」做矩阵，空白很明显：

| | 本地判题沙箱 | LLM 错因诊断 | 知识点画像 | 复习调度 | 桌面应用 |
|---|:---:|:---:|:---:|:---:|:---:|
| PrepDojo | ✅ | ✅（工具循环） | ❌ | ❌ | ❌（本地 Web） |
| LeetLens | ❌（用官方判题） | ✅ | ✅（脑图） | ✅（FSRS） | ✅（macOS 原生） |
| Recall 错题本 | ❌ | ✅ | ✅ | ✅（SM-2） | ❌（本地 Web） |
| Ankicode | ❌ | ❌ | ❌ | ✅（FSRS） | ✅（Tauri） |
| algo-sensei | ❌ | ✅（纯 prompt） | ❌ | ❌ | ❌ |
| OJ 系统（Hydro/Judge0…） | ✅ | ❌ | ❌ | ❌ | ❌ |
| **AlgoForge** | ✅ | ✅ | ✅ | ✅ | ✅（Electron） |

**结论**：AlgoForge 的差异化不是"又一个 OJ"，也不是"又一个 AI 讲解器"，
而是**把判题事实、LLM 诊断、能力画像、复习调度串成一条闭环**，并且用 LangGraph
显式编排这条链路。这个组合在开源生态里目前是空的。

**同时要注意两个正在被验证的威胁**：
1. **FSRS 正在取代 SM-2**。Ankicode、LeetLens、Smart-Study-Agent 都用了 FSRS。
   AlgoForge 的 SM-2 是 AGENTS.md 明确的教学模型选型，不算错，
   但 S10 时至少要知道 `py-fsrs`（488★）的存在，能一键升级。
2. **"AI 直接给答案"正在被反制**。CP-FOCUS（18★）这类"屏蔽 AI"的工具出现，
   说明"只给提示不给答案"是有市场共识的——AlgoForge 的苏格拉底式提示定位站在正确的一边。

---

## 5. 可以直接拿走的四样东西

1. **语料**：`labuladong/fucking-algorithm`（套路框架）+ `greyireland/algorithm-pattern`（算法模板）
   最贴合 AlgoForge `corpus/knowledge_cards/` 的粒度。注意版权边界，学 PrepDojo 的 BYO 模式。
2. **错因词表**：直接看 Recall 的受控词表设计（`概念不清/审题失误/粗心/计算错误/方法不当/超纲/其他`），
   它的理由是"避免统计被同义表达拆散"——这正是 P2-5 准确率卡在 50% 的根因方向。
3. **提示梯度**：algo-sensei 的 5 级渐进提示（观察 → 模式 → 方向 → 技巧 → 伪代码），
   可直接映射到 AlgoForge 的 prompt 模板变量。
4. **判定语义**：`QingdaoU/Judger`（seccomp 资源限制）与 `DMOJ/judge-server`（Python 判题流程），
   用来校准 TLE/MLE/RE 的边界定义。

---

## 6. 复现方式

检索用 GitHub REST API v3（匿名配额：search 10 次/分钟，core 60 次/小时），
脚本落在 `C:\Windows\Temp\`：`gh_search.py` ~ `gh_search4.py`（关键词/topic 检索）、
`gh_repos.py`（指定仓库精确核对）。

**有效检索式**（`in:name,description` 必须限定，否则巨型 awesome 榜单会淹没结果）：

```
topic:leetcode topic:llm
topic:online-judge
topic:spaced-repetition
topic:ai-tutor
"ai tutor" in:name,description stars:>50
leetcode desktop in:name,description
错题 in:name,description
mistake notebook in:name,description
competitive programming llm agent in:name,description
```

**反面经验**：`q=awesome leetcode in:name,description,readme` 加 `sort=stars` 的结果
全是 `tech-interview-handbook`、`public-apis` 这类百万★通用榜单，对定位同类系统毫无帮助。
定位"系统类"项目应该用 **topic 限定 + 明确的领域词**，而不是靠 awesome 关键词。
