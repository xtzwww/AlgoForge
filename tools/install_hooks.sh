#!/usr/bin/env bash
# ============================================================
# 安装 git 钩子（目前只有 pre-commit）。
#
#   bash tools/install_hooks.sh
#
# 为什么需要它：`.git/hooks/` **不进版本库**，所以钩子没法直接共享——
# 每个 clone 都要自己装一次。这个脚本把 `tools/hooks/` 下的模板复制过去，
# 模板本身在版本库里，可以随代码一起演进。
#
# 为什么需要钩子：远端是 Gitee，而 Gitee **不执行 `.github/workflows/`**，
# 所以那三个 job 从未真跑过。没有平台级 CI 时，提交前这一步是唯一的安全网。
#
# 卸载：rm .git/hooks/pre-commit
# ============================================================

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_SRC="$REPO/tools/hooks"
HOOK_DST="$REPO/.git/hooks"

if [ ! -d "$HOOK_DST" ]; then
  echo "找不到 $HOOK_DST —— 这个目录应该由 git 自动创建，确认这是不是一个 git 仓库。"
  exit 1
fi

installed=0
for src in "$HOOK_SRC"/*; do
  [ -f "$src" ] || continue
  name="$(basename "$src")"
  cp "$src" "$HOOK_DST/$name"
  chmod +x "$HOOK_DST/$name"
  echo "已安装钩子：$name"
  installed=$((installed + 1))
done

if [ "$installed" = "0" ]; then
  echo "$HOOK_SRC 下没有钩子模板。"
  exit 1
fi

echo
echo "完成。之后每次 git commit 会先跑：ruff + eslint（约几十秒）。"
echo "需要完整校验（含 pytest）时手动跑：bash tools/ci_local.sh"
echo "临时跳过钩子：git commit --no-verify"
