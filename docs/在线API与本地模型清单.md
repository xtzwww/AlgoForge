# AlgoForge 实际使用的在线 API 与本地模型清单

> 整理日期：2026-09-25
> 范围：当前代码仓库里**真正被调用**（或按配置就绪）的外部 API 与本地模型 / 本地服务。
> 判断依据：`sidecar/.env`、`sidecar/app/config.py`、`sidecar/app/llm/gateway.py`、
> `sidecar/app/model_catalog.py`、`sidecar/app/rag/{embedder,reranker,indexer}.py`，
> 以及前端 `desktop/src/renderer/src/composables/useTheme.ts` 等。

---

## 一、在线 API（外部云服务）

### 1. Agnes AI —— 主用大模型（错因分析）
- **端点**：`https://apihub.agnes-ai.cn/v1`
- **模型**：`agnes-3.0-flash`（**非** reasoning 模型）
- **用途**：判题后的「错因分析 / 修正提示」主路径 LLM 调用。走 OpenAI 兼容
  `POST /chat/completions`。
- **接入位置**：
  - 配置：`sidecar/.env` 的 `LLM_PROVIDER=openai_compatible` +
    `OPENAI_COMPATIBLE_BASE_URL / _API_KEY / _MODEL`
  - 代码：`sidecar/app/llm/gateway.py`（`_resolve_provider` 的 `openai_compatible` 分支、
    `_endpoint` 拼 `/chat/completions`）；`sidecar/app/model_catalog.py`（模型选择列表）
- **当前状态**：✅ 本机主用，`model_catalog.snapshot()` 返回 `agnes-3.0-flash`。
  实测 S2 准确率 12/20（60%），刚好压线。

### 2. 硅基流动 SiliconFlow —— 向量化 + 重排序
- **端点**：`https://api.siliconflow.cn/v1`
- **模型**：
  - Embedding：`BAAI/bge-m3`（1024 维，约 0.9s/次）
  - Rerank：`BAAI/bge-reranker-v2-m3`（cross-encoder）
- **用途**：RAG 知识库检索。**Embedding** 把语料与学生问题变成向量写入 Milvus；
  **Rerank** 在向量初筛 Top-10 后二次精排，取最相关的 3 条喂给分析模型（AGENTS.md S5）。
- **接入位置**：
  - 配置：`sidecar/.env` 的 `EMBEDDING_PROVIDER=openai_compatible` +
    `EMBEDDING_BASE_URL / EMBEDDING_MODEL / EMBEDDING_API_KEY`
  - 代码：
    - `sidecar/app/rag/embedder.py`：`/embeddings`（OpenAI 兼容）
    - `sidecar/app/rag/reranker.py`：`/rerank`（**复用 embedding 端点与密钥**，
      见 `build_reranker`）
- **当前状态**：✅ 已接入并实测可达（返回 3 条命中）。

### 3. DeepSeek —— 已配置但本机未激活
- **端点**：`https://api.deepseek.com`
- **模型**：`deepseek-coder`（代码默认模型）
- **用途**：设计上的「主路径首选」与 Ollama 的上一层，但**本机 `.env` 的
  `DEEPSEEK_API_KEY` 为空**，因此 `gateway._resolve_provider('deepseek')` 抛
  `LLMConfigError`，模型选择列表里不会出现，也不会被选中。
- **接入位置**：`config.py` 的 `deepseek_*`；`gateway.py` 的 `deepseek` 分支；
  切换只需在 `.env` 填 key 并将 `LLM_PROVIDER` 改为 `deepseek`。
- **当前状态**：⚪ 未激活（缺密钥）。属「配置就绪、按需启用」。

### 4. LangSmith —— 可选追踪（未启用）
- **用途**：S9 评估与调用链追踪（`sidecar/app/agent/tracing.py`）。
- **接入位置**：`config.py` 的 `langsmith_api_key / langsmith_project`。
- **当前状态**：⚪ `LANGSMITH_API_KEY` 为空，未启用。

---

## 二、本地模型 / 本地服务（本机运行）

### 5. Ollama —— 本地 LLM 兜底
- **端点**：`http://localhost:11434`
- **模型**：`qwen3.5:2b`（含 `-fullgpu` 变体），另有 `qwen3.5:0.8b`；
  可选 embedding 模型 `bge-m3`（但当前 `.env` 走 SiliconFlow）。
- **用途**：当主用在线端点（Agnes）不可用时启用，符合 AGENTS.md §一「主路径在线、
  兜底 Ollama」。实测 `qwen3.5:2b` 全 GPU 卸载约 45 tok/s。
- **接入位置**：`config.py` `ollama_*`；`sidecar/.env` `OLLAMA_MODEL=qwen3.5:2b`；
  `model_catalog.py`。
- **当前状态**：✅ 兜底就绪（需 Ollama 进程在跑）。

### 6. Milvus-lite —— 内嵌向量库
- **位置**：文件 `sidecar/data/milvus.db`（进程内运行，无需 Docker）
- **用途**：存放算法知识库向量，供 RAG 检索（AGENTS.md S5）。本机 Docker 不通，
  故用内嵌构建；能跑 Docker 的机器留空 `MILVUS_LITE_PATH` 改连 `MILVUS_URI` 服务。
- **接入位置**：`config.py` `milvus_lite_path / milvus_uri`；
  `sidecar/app/rag/{indexer,retriever}.py`；`embedder.resolve_milvus_uri()`。
- **当前状态**：✅ 已接入。

### 7. MinGW g++ —— C++ 判题
- **路径**：`D:\BaiduNetdiskDownload\dev\ucrt64\bin\g++.exe`（本机，`.env` 指定）
- **用途**：编译并运行 C++ 提交，`subprocess` + 超时 + 内存上限隔离（AGENTS.md：
  不自研沙箱、不用 Docker）。
- **接入位置**：`config.py` `cpp_compiler_path / cpp_std / sandbox_timeout`；
  `sidecar/.env` `CPP_COMPILER_PATH`。
- **当前状态**：✅ 本机已配。

### 8. JDK 1.8（javac / java）—— Java 判题
- **路径**：`C:\Program Files\Java\jdk1.8.0_291\bin\javac.exe`（本机，不在 PATH，显式指定）
- **用途**：编译并运行 Java 提交（需 JDK 而非 JRE，因为要 `javac`）。
- **接入位置**：`config.py` `java_compiler_path / java_runtime_path / java_max_heap_mb`；
  `sidecar/.env` `JAVA_COMPILER_PATH`。
- **当前状态**：✅ 本机已配。

### 9. Python 解释器 —— Python 判题
- **取值**：`sidecar/.env` `PYTHON_INTERPRETER_PATH` 留空 → 用 sidecar 自身 venv 的解释器。
- **用途**：运行 / 语法检查 Python 提交。
- **接入位置**：`config.py` `python_interpreter_path` + `resolved_python_interpreter` 属性。
- **当前状态**：✅ 本机已配（sidecar venv）。

### 10. PostgreSQL —— 业务数据库（本地服务，非模型）
- **端点**：`localhost:5432`，库 `algoforge`
- **用途**：业务数据 + LangGraph 检查点 + 长期记忆 Store（AGENTS.md §一）。
- **接入位置**：`config.py` `database_url / langgraph_checkpoint_uri`；`sidecar/.env`。
- **当前状态**：✅ 本地服务运行中（start-web.bat 负责拉起）。

---

## 三、状态速览

| 名称 | 类型 | 用途 | 状态 |
|---|---|---|---|
| Agnes AI (agnes-3.0-flash) | 在线 API | 主用错因分析 LLM | ✅ 主用 |
| SiliconFlow (bge-m3 / bge-reranker-v2-m3) | 在线 API | 向量化 + 重排序 | ✅ 已接入 |
| DeepSeek (deepseek-coder) | 在线 API | 设计主路径（缺 key） | ⚪ 未激活 |
| LangSmith | 在线 API | 调用追踪（缺 key） | ⚪ 未启用 |
| Ollama (qwen3.5:2b) | 本地模型 | LLM 兜底 | ✅ 兜底就绪 |
| Milvus-lite | 本地服务 | 向量库 | ✅ 已接入 |
| MinGW g++ | 本地工具 | C++ 判题 | ✅ 已配 |
| JDK 1.8 | 本地工具 | Java 判题 | ✅ 已配 |
| Python 解释器 | 本地工具 | Python 判题 | ✅ 已配 |
| PostgreSQL | 本地服务 | 业务库 / 检查点 | ✅ 运行中 |

> **结论**：真正「在跑、对外付费」的只有 **Agnes AI** 和 **SiliconFlow** 两家在线 API；
> DeepSeek / LangSmith 仅配置就绪、本机未激活。本地侧以 **Ollama（LLM 兜底）** 为核心模型，
> 配合 **Milvus-lite** 向量库与 **g++ / JDK1.8 / Python** 三套判题编译器。
