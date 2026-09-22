#!/usr/bin/env bash
# ============================================================
# 打 sidecar（阶段 7 · 打包与交付，PyInstaller onedir）。
#
#   bash sidecar/packaging/build_sidecar.sh
#
# 产物：dist/algoforge-sidecar/algoforge-sidecar.exe
#
# 三个不能省的参数（都是踩出来的）
# --------------------------------
# 1. `--add-data "app/prompts/*.md;app/prompts"`：提示模板是运行时读的**文件**
#    （AGENTS.md §二.1 的红线：prompt 不许硬编码）。PyInstaller 只跟 import，
#    不跟 open()，少了这一行打包版一点「分析」就会报 PromptNotFoundError。
# 2. `--workpath C:/Windows/Temp/...`：中文用户名让 %TEMP% 成非 ASCII 路径，
#    PyInstaller 的部分工具链在这种路径上会失败。
# 3. `--distpath ../dist`：`dist/` 已在 .gitignore 里，产物不会被误提交。
#
# 不打包的东西：`.env`（密钥不进产物，见 README §六 阶段 7 完成定义第 4 条）。
# 打包后配置走环境变量，或用 `ALGOFORGE_ENV_FILE` 指向外部的 .env。
# ============================================================
set -euo pipefail

# `pwd -W` 而不是 `pwd`：Git Bash 的 `pwd` 给的是 `/d/BaiduNetdiskDownload/...`，
# 传给 PyInstaller 会变成 `\d\BaiduNetdiskDownload\...`（盘符被当成目录），
# 报 "Unable to find"。`-W` 才是 Windows 路径。
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W)"
cd "$REPO/sidecar"

VENV_PY="${VENV_PY:-./.venv/Scripts/python.exe}"
WORKPATH="${WORKPATH:-C:/Windows/Temp/pyinstaller_work}"

# 跑 Python 的唯一入口：在子 shell 里清掉 PYTHONPATH 再 exec。
#
# ⚠️ **不要写成 `env -u PYTHONPATH ...`**。本机 `~/.local/bin/env` 覆盖了
# `/usr/bin/env`，而它是个只往 PATH 里加 `~/.local/bin` 的空壳脚本：**忽略
# 全部参数、什么都不执行、还返回 0**。于是整条 PyInstaller 命令变成空操作，
# 脚本照样打印「产物：…」并退出 0 —— 构建根本没发生，`set -e` 也抓不住。
# `tools/ci_local.sh` 踩过同一个坑（见其文件头第 2 条），这里是同一个修法。
#
# PYTHONPATH 又**必须**清掉：sitecustomize.py 会让 PyInstaller 的清理阶段
# 抛 SystemExit。
py() { ( unset PYTHONPATH; exec "$VENV_PY" "$@" ); }

echo "[1/2] 清理上一次的产物"
rm -rf "$REPO/dist/algoforge-sidecar" "$WORKPATH"

# `--add-data` 的相对路径是相对 **spec 文件所在目录**（这里是 packaging/），
# 不是 CWD；而且 Windows 下它**不展开通配符**（实测报
# "Unable to find ...\app\prompts\*.md"）。所以逐个文件给绝对路径。
ADD_DATA=()
for f in "$REPO"/sidecar/app/prompts/*.md; do
  ADD_DATA+=(--add-data "$f;app/prompts")
done
echo "      打包数据文件：${#ADD_DATA[@]} 个提示模板"

# ---- 语料：题目 / 知识卡片 / 评估集 -------------------------------------
#
# ⚠️ 这三样**曾经没被打进包**，而它们的默认路径是按 `__file__` 算的 ——
# 冻结后 `__file__` 在产物内部，于是打包版既找不到题目也找不到知识卡片，
# 且**一声不响**（检索降级成空上下文、题库在首次使用时才抛 FileNotFound）。
# 2026-09-22 由 `--self-check-retrieval` 报出来，此前所有 import 级检查
# 全绿。修法有两半：路径改用 `app.config.DATA_ROOT`（见 config._project_root），
# 文件在这里跟着进包。
#
# 目标目录写成 `./corpus/...`：`--add-data` 的目标是相对 **bundle 目录**
# （onedir 下就是 `_internal/`）的，而 `app.config.DATA_ROOT` 冻结时正是
# `sys._MEIPASS`，即同一个目录 —— 两边对得上，语料就落在
# `DATA_ROOT/corpus/...`，与源码树里的相对位置一致。
#
# ⚠️ 别写 `../corpus`：PyInstaller 会拒绝（"DEST_DIR must not point outside
# of application's top-level directory"），因为 `--add-data` 不允许写到包外。
# 2026-09-22 实测踩到，同一轮才把 DATA_ROOT 从「exe 所在目录」改成 `_MEIPASS`。
for dirname in problems knowledge_cards eval_cases; do
  src="$REPO/corpus/$dirname"
  if [ -d "$src" ]; then
    ADD_DATA+=(--add-data "$src;corpus/$dirname")
    echo "      打包语料：corpus/$dirname"
  else
    echo "⚠️  缺少语料目录：$src" >&2
  fi
done

# ---- 懒导入的模块：显式声明，别指望字节码扫描 --------------------------
#
# 下面这些模块在源码里都是**函数体内**才 import 的：
#   pipeline.py  → app.agent.graph / app.agent.team（只在非默认流水线分支里）
#   retriever.py → pymilvus（只有真的要连 Milvus 时才 import）
#   memory.py    → langgraph.checkpoint.postgres / langgraph.store.postgres
#
# ⚠️ 2026-09-21 实测**推翻了**「懒导入所以 PyInstaller 收不到」这个说法：
# 解出上一版产物的 PYZ 归档数过——`langgraph` 126 个模块、`pymilvus` 68 个
# （含 `pymilvus.milvus_client.milvus_client`，正是 `MilvusClient` 所在）、
# `langsmith` 229 个、`psycopg` 80 个**全都在**。PyInstaller 扫的是字节码，
# 函数体内的 `IMPORT_NAME` 一样跟得到。
#
# 那为什么还要显式写？因为收得到靠的是**字节码扫描这个偶然行为**。任何一处
# 改成 `importlib.import_module("...")`、或把名字挂在 `__getattr__` 后面转发，
# 扫描就断——而断了的症状是**打包时全绿、运行时才 ImportError**，正是最难
# 查的那一类。显式列出来把「这几个必须进包」变成打包期的契约。
# 代价是零：这些模块本来就在产物里（体积不变），只是从「碰巧收到」变成「要求收到」。
LAZY_IMPORTS=(
  "app.agent.graph"                # S4 编排图（pipeline.py 的 graph 分支）
  "app.agent.team"                 # S7 三智能体（pipeline.py 的 team 分支）
  "pymilvus"                       # S5 检索层（retriever.py 连 Milvus 时才用）
  "langgraph.checkpoint.postgres"  # S8 短期记忆落 PG
  "langgraph.store.postgres"       # S8 长期记忆落 PG
)
HIDDEN=()
for m in "${LAZY_IMPORTS[@]}"; do
  HIDDEN+=(--hidden-import "$m")
done
echo "      显式声明懒导入：${#LAZY_IMPORTS[@]} 个模块"

echo "[2/2] PyInstaller 构建（约数分钟）"
py -m PyInstaller \
  --noconfirm --clean --onedir \
  --name algoforge-sidecar \
  --distpath "$REPO/dist" \
  --workpath "$WORKPATH" \
  --specpath packaging \
  "${ADD_DATA[@]}" \
  "${HIDDEN[@]}" \
  --hidden-import "uvicorn.logging" \
  --hidden-import "sqlalchemy.dialects.postgresql.asyncpg" \
  --hidden-import "asyncpg" \
  --hidden-import "milvus_lite" \
  --hidden-import "faiss" \
  --copy-metadata "milvus-lite" \
  --log-level WARN \
  packaging/sidecar_main.py

# ---- 内嵌向量库：带上 milvus-lite（2026-09-22 决定）---------------------
#
# 这里曾经同时排除 `milvus_lite` / `faiss` / `pyarrow`，理由是「sidecar 生产
# 走 Milvus **服务**（MILVUS_URI），永远不 import 它」。**那个理由与代码和
# 配置都不符**：`settings.milvus_lite_path` 默认指向 `sidecar/data/milvus.db`，
# `.env` 也没把它留空，所以运行时走的一直是内嵌引擎。结果是打包版**两条路
# 都不通** —— 没有 `milvus_lite` 包，也没有 Milvus 服务 —— 检索只能静默降级，
# 界面上「参考资料」永远是空的。
#
# 现在按用户 2026-09-22 的决定：**内嵌库打进产物**，目标机不需要 Docker、
# 不需要起服务，开箱即用。
#
# ⚠️ **`pyarrow` 不能排除**，这也是 2026-09-22 实测推翻的一条旧结论：
# `milvus_lite/engine/collection.py` 在**模块级** `import pyarrow`，排除它
# 会让 `import milvus_lite` 直接 `ModuleNotFoundError: No module named
# 'pyarrow'` —— 而 `pymilvus` 把这个 ImportError 翻译成
# 「milvus-lite is required for local database connections，请 pip install
# pymilvus[milvus_lite]」，指向一个**已经装好的包**，误导性极强。
# 排查办法：`algoforge-sidecar.exe --probe-import milvus_lite.server_manager`
# 会打印真实异常链（这条参数就是为这次排查留下的）。
#
# 所以：内嵌引擎的真实代价是 `milvus_lite`(1.8 MB) + `faiss`(15 MB) +
# `pyarrow`(85.6 MB) ≈ 102 MB，另加一条 `--copy-metadata`（见下）。
#
# ⚠️ `--copy-metadata "milvus-lite"` 也是必需的，这是第三个坑：
# `milvus_lite/_version.py` 的 ``get_version()`` 先找源码树里的
# ``pyproject.toml``（打包后没有），再退回 ``importlib.metadata.version()``。
# PyInstaller 默认**只打包代码、不带包的元数据**，于是那一步抛
# ``PackageNotFoundError: No package metadata was found for milvus-lite`` ——
# 又一个被 pymilvus 吞成「请 pip install」的错误。
# ``--copy-metadata`` 把 ``*.dist-info`` 一并带进产物，问题消失。
# 名字要用**发行名** ``milvus-lite``（连字符），不是 import 名 ``milvus_lite``。

# 同理别去改 packaging/*.spec：它是每次构建重新生成的（--noconfirm），
# 而且 .gitignore 里就排除了 *.spec，改了不进版本库。

echo ""
echo "产物：$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe"
echo "验证："
echo "  SIDECAR_TOKEN=dev \"$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe\" --port 8123"
echo "  curl --noproxy '*' http://127.0.0.1:8123/health"
echo "  冻结后能否 import 整条分析链路（无模型调用、无数据库）："
echo "  \"$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe\" --self-check"
