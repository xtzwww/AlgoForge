#!/usr/bin/env bash
# ============================================================
# 本地 CI —— 把「记得跑的时候才跑」的校验变成一条命令。
#
#   bash tools/ci_local.sh          # 全量：ruff + eslint + pytest
#   bash tools/ci_local.sh --fast   # 快速：只跑 ruff + eslint（pre-commit 用这个）
#
# 为什么需要这个文件
# ------------------
# `.github/workflows/ci.yml` 写了三个 job，但**从未在任何平台真跑过**：
# 远端是 Gitee，而 Gitee 不执行 `.github/workflows/`。于是 900 多个测试
# 实际上没有东西在每次提交后自动跑——它们是「手动执行时才存在」的安全网。
# 这个脚本把 ci.yml 的检查步骤在本机复刻一遍，让「提交前跑一次」变成一条命令。
#
# 三条硬约束（与 ci.yml 保持一致，改之前先读）
# -------------------------------------------
#   1. **绝不跑 eval_set。** 那批会真请求付费 LLM 端点，既计费又不可复现。
#      所以 pytest 永远带 `-m "not eval_set"`。
#   2. **跑 Python 必须 `env -u PYTHONPATH`。** 否则 sitecustomize.py 会让清理
#      阶段的 `Path.unlink` 抛 SystemExit(1)，表现为一大批与改动无关的假失败。
#   3. **前端工具直接调 `./node_modules/.bin/*`，不要用 `npm run`。**
#      本机 `npm run` 会静默 exit 1 且一个字都不输出。
#
# 注意 eslint **只扫 src**：`eslint .` 会去扫 dist-web/ 产物，十分钟跑不完。
# ============================================================

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

FAST=0
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
  esac
done

# 优先用项目自己的 venv：与 sidecar 运行环境一致，且 ruff 也在里面。
PY="sidecar/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="python"
RUFF="sidecar/.venv/Scripts/ruff.exe"
[ -x "$RUFF" ] || RUFF="$(command -v ruff || true)"

failed=0
step() { echo; echo "=== $1 ==="; }

step "Ruff（后端规范）"
if [ -n "$RUFF" ]; then
  "$RUFF" check . || failed=1
else
  echo "跳过：未找到 ruff（可 pip install ruff）"
fi

step "ESLint（前端规范，只扫 src）"
if [ -x desktop/node_modules/.bin/eslint ]; then
  ( cd desktop && ./node_modules/.bin/eslint src --no-cache ) || failed=1
else
  echo "跳过：desktop/node_modules 未安装"
fi

# 语料校验放在这里而不是只留一个脚本：写了校验没人跑，等于没写。
# 它是纯解析，很快，值得每次提交都过一遍。
step "知识卡片语料（corpus/knowledge_cards/）"
env -u PYTHONPATH "$PY" tools/validate_knowledge_cards.py || failed=1

if [ "$FAST" = "1" ]; then
  echo
  echo "（--fast 模式跳过 pytest。提交前想跑全量：bash tools/ci_local.sh）"
else
  step "pytest（排除 eval_set）"
  echo "提示：全量在本机可能要一二十分钟（真实 C++ 编译慢，进程创建开销高）。"
  env -u PYTHONPATH "$PY" -u -m pytest -q -m "not eval_set" || failed=1
fi

echo
if [ "$failed" = "0" ]; then
  echo "本地 CI 通过。"
else
  echo "本地 CI 有失败项，见上方输出。"
fi
exit "$failed"
