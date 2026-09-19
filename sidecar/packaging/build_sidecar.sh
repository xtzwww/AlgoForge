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

echo "[2/2] PyInstaller 构建（约数分钟）"
env -u PYTHONPATH "$VENV_PY" -m PyInstaller \
  --noconfirm --clean --onedir \
  --name algoforge-sidecar \
  --distpath "$REPO/dist" \
  --workpath "$WORKPATH" \
  --specpath packaging \
  "${ADD_DATA[@]}" \
  --hidden-import "uvicorn.logging" \
  --hidden-import "sqlalchemy.dialects.postgresql.asyncpg" \
  --hidden-import "asyncpg" \
  --log-level WARN \
  packaging/sidecar_main.py

echo ""
echo "产物：$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe"
echo "验证："
echo "  SIDECAR_TOKEN=dev \"$REPO/dist/algoforge-sidecar/algoforge-sidecar.exe\" --port 8123"
echo "  curl --noproxy '*' http://127.0.0.1:8123/health"
