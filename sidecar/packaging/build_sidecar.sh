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
  --exclude-module "milvus_lite" \
  --exclude-module "faiss" \
  --exclude-module "pyarrow" \
  --log-level WARN \
  packaging/sidecar_main.py

# ⚠️ 那两个 --exclude-module 是必需的，不是洁癖。
# `pip install milvus-lite` 会带进 faiss-cpu 与 pyarrow（上百 MB），而它只是
# tests/test_rag_milvus_live.py 用来对着真实 Milvus 引擎验检索层的**测试**依赖
# ——sidecar 生产走的是 Milvus **服务**（MILVUS_URI），永远不 import 它。
# PyInstaller 做的是静态扫描，不看 import 是否真的会执行，所以只要某个开发者
# 装过 milvus-lite，产物就会莫名胖上百 MB。显式排除后，产物体积不再取决于
# 谁跑过那条测试。
# 同理别去改 packaging/*.spec：它是每次构建重新生成的（--noconfirm），
# 而且 .gitignore 里就排除了 *.spec，改了不进版本库。
#
# `--exclude-module pyarrow` 与上面两个同一个道理，但它是 2026-09-21 才发现的：
# 一旦显式 `--hidden-import pymilvus`，PyInstaller 就会扫到
# `pymilvus/bulk_writer/`，而那里**模块级** `import pyarrow`（buffer.py:21、
# constants.py:16）——实测产物因此从 **113 MB 涨到 194 MB**，多出来的 81 MB
# 全是 `pyarrow/`（81 MB）+ `pyarrow.libs/`（1 MB）。
#
# bulk_writer 是 pymilvus 的「批量导入」功能，AlgoForge 用的是 MilvusClient 的
# 建库/检索，永远不碰它；`pymilvus/client/type_info.py` 还专门写了注释说自己
# **不** import pyarrow（"Return symbolic Arrow layout facts without importing
# pyarrow"）。所以排除是安全的——已实测：屏蔽 pyarrow 后
# `from pymilvus import MilvusClient` 仍然成功（pymilvus 3.0.2）。
# 排除后产物回到 ~113 MB 量级。

echo ""
echo "产物：$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe"
echo "验证："
echo "  SIDECAR_TOKEN=dev \"$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe\" --port 8123"
echo "  curl --noproxy '*' http://127.0.0.1:8123/health"
echo "  冻结后能否 import 整条分析链路（无模型调用、无数据库）："
echo "  \"$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe\" --self-check"
