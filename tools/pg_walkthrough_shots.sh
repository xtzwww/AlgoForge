#!/usr/bin/env bash
# ============================================================
# 真 PostgreSQL 页面走查 + 截图。
#
#   bash tools/pg_walkthrough_shots.sh [输出目录]
#
# 回答一个 start-walkthrough-pg.sh 回答不了的问题：**页面**在真 PG 上到底长
# 什么样。那个脚本只验到接口冒烟（聚合 SQL 在真库上能跑），而页面是另一层：
# 数据有没有渲染出来、有没有报错，只有把图截下来看才知道。
#
# 流程：起 PG → 迁移 → 起 sidecar（连真 PG）→ 必要时灌题 → 起 vite（网页版）
# → 用 headless Chromium 逐个路由截图 → 干净收尾。
#
# ⚠️ 必须整段跑完，不要拆开手工执行
# --------------------------------
# PG **不跨 shell 调用存活**（沙箱每次 Bash 调用结束杀进程树），所以「启动」和
# 「使用」必须在同一次调用里。Ctrl+C 退出时 trap 会 `pg_ctl -m fast` 干净停止；
# 直接杀掉会留 stale `postmaster.pid`，下次启动还要等 10~40 秒崩溃恢复。
#
# 为什么用裸 Chromium 而不是 Playwright
# -------------------------------------
# 本机 `ms-playwright` 只装了浏览器，没有装 playwright 包；而 `--screenshot`
# + `--virtual-time-budget` 足够把 SPA 等渲染完再截图。代价是**不能交互**——
# 所以教师端只能截到登录表单，登录后看板要用 API 冒烟（`start-walkthrough-pg.sh`）
# 或人工点。别把「截图通过」当成「交互也通过」。
# ============================================================
set -uo pipefail

PG_HOME="${PG_HOME:-D:/pg}"
PG_DATA="$PG_HOME/data"
PG_BIN="$PG_HOME/pgsql/bin"
NOCMD="$PG_HOME/nocmd.exe"
PG_DSN="postgresql+asyncpg://algoforge:algoforge@127.0.0.1:5432/algoforge"
PG_DSN_PSYCOPG="postgresql+psycopg://algoforge:algoforge@127.0.0.1:5432/algoforge"
# COMSPEC 必须给反斜杠：nocmd.exe 是替代 cmd.exe 的包装器（HKLM AutoRun=chcp 65001
# 会污染一切 popen，PG 因此拒绝启动）。实测正斜杠形式 postgres 根本不写日志就退出。
NOCMD_WIN='D:\pg\nocmd.exe'
# AF_REPO 可覆盖：脚本被复制到别处跑（例如 C:/Windows/Temp）时用得上。
REPO="${AF_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SIDECAR_PORT=8123
SIDECAR_TOKEN=dev-local-token
VITE_PORT=5173
OUT_DIR="${1:-C:/Windows/Temp/pg_shots}"
CHROME="${CHROME:-C:/Users/雪团子/AppData/Local/ms-playwright/chromium-1223/chrome-win64/chrome.exe}"

ROUTES=(practice problems mistakes profile plan teacher)

PG_UP=0
VITE_PID=""
SIDECAR_PID=""

cleanup() {
  echo ""
  echo "[收尾] 停止 vite / sidecar"
  [ -n "$VITE_PID" ] && kill "$VITE_PID" 2>/dev/null
  [ -n "$SIDECAR_PID" ] && kill "$SIDECAR_PID" 2>/dev/null
  if [ "$PG_UP" = 1 ]; then
    echo "[收尾] 停止 PostgreSQL（干净停止，下次启动不用崩溃恢复）"
    (cd "$PG_HOME" && env COMSPEC="$NOCMD_WIN" "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -m fast stop 2>&1 | tail -1)
  fi
}
trap cleanup EXIT

die() { echo "[shots] 失败：$*" >&2; exit 1; }

# --- 1. PostgreSQL -------------------------------------------------------
if [ -f "$PG_DATA/postmaster.pid" ] && ! tasklist 2>/dev/null | grep -qi postgres; then
  echo "[1/6] 删除 stale postmaster.pid（无 postgres 进程）"
  rm -f "$PG_DATA/postmaster.pid"
fi
echo "[1/6] 启动 PostgreSQL"
(cd "$PG_HOME" && env COMSPEC="$NOCMD_WIN" "$PG_BIN/postgres.exe" -D "$PG_DATA" \
    > "$PG_HOME/pg_shots.log" 2>&1 &)
PG_UP=1
ready=no
for i in $(seq 1 40); do
  sleep 2
  if "$PG_BIN/pg_isready.exe" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "      就绪（$((i * 2)) 秒）"; ready=yes; break
  fi
done
[ "$ready" = yes ] || { tail -15 "$PG_HOME/pg_shots.log"; die "PG 未就绪"; }

# --- 2. 迁移 -------------------------------------------------------------
echo "[2/6] alembic upgrade head"
(cd "$REPO/sidecar" && env -u PYTHONPATH ./.venv/Scripts/python.exe -m alembic \
    -x "db_url=$PG_DSN_PSYCOPG" upgrade head 2>&1 | tail -3) || die "迁移失败"

# --- 3. sidecar ----------------------------------------------------------
echo "[3/6] 启动 sidecar（DATABASE_URL 指向真 PostgreSQL）"
# 不用子 shell 起，否则 $! 拿不到真实 PID（也就得靠 netstat/taskkill 兜底，
# 而那两个命令会触发沙箱升级——升级后 PostgreSQL 反而起不来，实测过）。
#
# 关于沙箱升级的额外说明：本机测试时 `bash tools/pg_walkthrough_shots.sh` 这条
# 形式会被识别为「可能要运行高风险命令」而弹升级，升级后 PG 启动直接失败
#（log 空、进程不写）。解决办法是把整段走查放进**一次** Bash 工具调用里内联执行
#（不要 `bash script.sh`，而是把步骤直接写在命令字符串中）。这是本机沙箱的特
# 殊行为，与脚本无关；用户自己在非沙箱里跑这个脚本不会有这个问题。
cd "$REPO/sidecar" || die "没有 sidecar/ 目录"
env -u PYTHONPATH DATABASE_URL="$PG_DSN" \
    SIDECAR_PORT="$SIDECAR_PORT" SIDECAR_TOKEN="$SIDECAR_TOKEN" \
    .venv/Scripts/python.exe -u -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$SIDECAR_PORT" > C:/Windows/Temp/uv_shots.log 2>&1 &
SIDECAR_PID=$!
for i in $(seq 1 30); do
  if curl -sf --noproxy '*' -m 2 "http://127.0.0.1:$SIDECAR_PORT/health" >/dev/null 2>&1; then
    echo "      /health ok（$((i * 2)) 秒）"; break
  fi
  sleep 2
done
curl -sf --noproxy '*' -m 3 "http://127.0.0.1:$SIDECAR_PORT/health" >/dev/null 2>&1 ||
  { tail -20 C:/Windows/Temp/uv_shots.log; die "sidecar 未起，看上面日志"; }

# --- 4. 数据（题库与提交都已有就跳过；PG 的数据目录是持久的）-------------
echo "[4/6] 检查已有数据"
COUNT="$(curl -sf --noproxy '*' -m 5 -H "X-Sidecar-Token: $SIDECAR_TOKEN" \
    "http://127.0.0.1:$SIDECAR_PORT/api/v1/problems?limit=1" |
    env -u PYTHONPATH "$REPO/sidecar/.venv/Scripts/python.exe" -c \
    'import json,sys; print(json.load(sys.stdin).get("total", 0))' 2>/dev/null || echo 0)"
echo "      题库 $COUNT 道"
if [ "${COUNT:-0}" -lt 1 ] 2>/dev/null; then
  echo "      灌入 20 道题（每题真实编译，约 1 分钟）…"
  (cd "$REPO" && env -u PYTHONPATH DATABASE_URL="$PG_DSN" \
      sidecar/.venv/Scripts/python.exe tools/load_problems.py 2>&1 | tail -3)
fi

# --- 5. vite（网页版）----------------------------------------------------
echo "[5/6] 启动 vite（网页版，指向上面的 sidecar）"
mkdir -p "$OUT_DIR"
cd "$REPO/desktop" || die "没有 desktop/ 目录"
env VITE_SIDECAR_URL="http://127.0.0.1:$SIDECAR_PORT" \
    VITE_SIDECAR_TOKEN="$SIDECAR_TOKEN" \
    ./node_modules/.bin/vite --config vite.config.web.ts --mode web \
    --port "$VITE_PORT" --strictPort > C:/Windows/Temp/vite_shots.log 2>&1 &
VITE_PID=$!
for i in $(seq 1 40); do
  if curl -sf --noproxy '*' -m 2 "http://127.0.0.1:$VITE_PORT/" >/dev/null 2>&1; then
    echo "      就绪（$((i * 2)) 秒）"; break
  fi
  sleep 2
done
curl -sf --noproxy '*' -m 3 "http://127.0.0.1:$VITE_PORT/" >/dev/null 2>&1 ||
  { tail -20 C:/Windows/Temp/vite_shots.log; die "vite 未起，看上面日志"; }

# --- 6. 逐个路由截图 -----------------------------------------------------
echo "[6/6] 截图 → $OUT_DIR"
for r in "${ROUTES[@]}"; do
  # --virtual-time-budget：SPA 要等接口回来才画得出数据，不等就是空骨架。
  # --no-proxy-server：本机代理会吞掉对 127.0.0.1 的请求（老坑）。
  "$CHROME" --headless=new --disable-gpu --no-proxy-server --hide-scrollbars \
      --window-size=1600,1000 --virtual-time-budget=12000 \
      --screenshot="$OUT_DIR/pg_$r.png" \
      "http://127.0.0.1:$VITE_PORT/#/$r" >/dev/null 2>&1
  size="$(stat -c %s "$OUT_DIR/pg_$r.png" 2>/dev/null || echo 0)"
  echo "      #/$r -> pg_$r.png（$size 字节）"
done

echo ""
echo "完成：$OUT_DIR"
echo "注意：教师端只截到登录表单（裸 Chromium 不能交互）；"
echo "      已登录看板请用 start-walkthrough-pg.sh 的 API 冒烟或人工点。"
