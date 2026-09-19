#!/usr/bin/env bash
# ============================================================
# 真 PostgreSQL 版走查启动脚本。
#
#   bash start-walkthrough-pg.sh            # 起全套，最后跑 vite（浏览器走查）
#   bash start-walkthrough-pg.sh --no-web   # 只做冒烟：起服务 + 灌题 + 验证 API
#
# 与 `start-walkthrough.sh` 的区别：那个用 SQLite 绕开数据库，这个连的是
# **真实 PostgreSQL 17.6**（`D:\pg`）。SQLite 不会替你发现方言问题——它默认
# 不强制外键、函数名也不同——所以「页面在真库上到底能不能跑」只有这个脚本能回答。
#
# ⚠️ 必须整段跑完，不要拆成几条命令手工执行
# ----------------------------------------
# PG **不跨 shell 调用存活**（沙箱每次调用结束杀进程树）。所以「启动 PG」和
# 「使用 PG」必须在同一次调用里——本脚本就是那一次调用。Ctrl+C 退出时 trap 会
# 用 `pg_ctl -m fast` 干净停止，下次启动就只要 2 秒；直接杀掉的话会留下 stale
# `postmaster.pid`（pg_ctl 因此拒绝启动且日志全空），下次还得先删它、并等
# 10~40 秒的崩溃恢复。
#
# 退出后剩下的东西
# ----------------
# 数据库里有 20 道题目和若干提交，想重来就删库重建（`dropdb` / `createdb`）
# 或跑 `./tools/pg_verify.sh`（它会清空后重跑迁移与聚合测试）。
# ============================================================
set -uo pipefail

PG_HOME="${PG_HOME:-D:/pg}"
PG_DATA="$PG_HOME/data"
PG_BIN="$PG_HOME/pgsql/bin"
NOCMD="$PG_HOME/nocmd.exe"
PG_DSN="postgresql+asyncpg://algoforge:algoforge@127.0.0.1:5432/algoforge"
PG_DSN_PSYCOPG="postgresql+psycopg://algoforge:algoforge@127.0.0.1:5432/algoforge"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_PORT=8123
SIDECAR_TOKEN=dev-local-token
NO_WEB=0
[ "${1:-}" = "--no-web" ] && NO_WEB=1

PG_UP=0
SIDECAR_PID=""

cleanup() {
  echo ""
  echo "[收尾] 停止 sidecar"
  [ -n "$SIDECAR_PID" ] && kill "$SIDECAR_PID" 2>/dev/null
  if [ "$PG_UP" = 1 ]; then
    echo "[收尾] 停止 PostgreSQL（干净停止，下次启动才不用崩溃恢复）"
    (cd "$PG_HOME" && env COMSPEC="$NOCMD" "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -m fast stop 2>&1 | tail -1)
  fi
}
trap cleanup EXIT

die() { echo "[pg-walkthrough] 失败：$*" >&2; exit 1; }

# --- 1. PostgreSQL -------------------------------------------------------
if [ -f "$PG_DATA/postmaster.pid" ] && ! tasklist 2>/dev/null | grep -qi postgres; then
  echo "[1/5] 删除 stale postmaster.pid（无 postgres 进程）"
  rm -f "$PG_DATA/postmaster.pid"
fi
echo "[1/5] 启动 PostgreSQL"
(cd "$PG_HOME" && env COMSPEC="$NOCMD" "$PG_BIN/postgres.exe" -D "$PG_DATA" \
    > "$PG_HOME/pg_walkthrough.log" 2>&1 &)
PG_UP=1
ready=no
for i in $(seq 1 40); do
  sleep 2
  if "$PG_BIN/pg_isready.exe" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "      就绪（$((i * 2)) 秒）"; ready=yes; break
  fi
done
[ "$ready" = yes ] || { tail -15 "$PG_HOME/pg_walkthrough.log"; die "PG 未就绪"; }

# --- 2. 迁移 -------------------------------------------------------------
echo "[2/5] alembic upgrade head"
(cd "$REPO/sidecar" && env -u PYTHONPATH ./.venv/Scripts/python.exe -m alembic \
    -x "db_url=$PG_DSN_PSYCOPG" upgrade head 2>&1 | tail -3) || die "迁移失败"

# --- 3. sidecar ----------------------------------------------------------
echo "[3/5] 启动 sidecar（DATABASE_URL 指向真 PostgreSQL）"
(cd "$REPO/sidecar" && env -u PYTHONPATH DATABASE_URL="$PG_DSN" \
    SIDECAR_PORT="$SIDECAR_PORT" SIDECAR_TOKEN="$SIDECAR_TOKEN" \
    .venv/Scripts/python.exe -u -m uvicorn app.main:app \
    --host 127.0.0.1 --port "$SIDECAR_PORT" > C:/Windows/Temp/uv_pg.log 2>&1 &)
# 注意：上面是子 shell 起的进程，$! 拿不到它，按端口兜底。
for i in $(seq 1 30); do
  if curl -sf --noproxy '*' -m 2 "http://127.0.0.1:$SIDECAR_PORT/health" >/dev/null 2>&1; then
    echo "      /health ok（$((i * 2)) 秒）"; break
  fi
  sleep 2
done
curl -sf --noproxy '*' -m 3 "http://127.0.0.1:$SIDECAR_PORT/health" || {
  tail -20 C:/Windows/Temp/uv_pg.log; die "sidecar 未起，看上面日志"; }
SIDECAR_PID="$(netstat -ano 2>/dev/null | grep "127.0.0.1:$SIDECAR_PORT" | grep LISTENING |
    awk '{print $NF}' | head -1)"

# --- 4. 灌题（库空时才灌，约 1 分钟） -----------------------------------
COUNT="$(curl -sf --noproxy '*' -m 5 -H "X-Sidecar-Token: $SIDECAR_TOKEN" \
    "http://127.0.0.1:$SIDECAR_PORT/api/v1/problems" |
    env -u PYTHONPATH "$REPO/sidecar/.venv/Scripts/python.exe" -c \
    'import json,sys; print(json.load(sys.stdin).get("total", 0))' 2>/dev/null || echo 0)"
echo "[4/5] 题库现有 $COUNT 道"
if [ "${COUNT:-0}" -lt 1 ] 2>/dev/null; then
  echo "      灌入 20 道题（每题真实编译，约 1 分钟）…"
  (cd "$REPO" && env -u PYTHONPATH DATABASE_URL="$PG_DSN" \
      sidecar/.venv/Scripts/python.exe tools/load_problems.py 2>&1 | tail -3)
fi

# --- 5. 冒烟：业务 API 在真 PG 上真的能用 --------------------------------
echo "[5/5] 冒烟验证（真实 PostgreSQL）"
env -u PYTHONPATH "$REPO/sidecar/.venv/Scripts/python.exe" - <<PY || die "冒烟失败"
import json, urllib.request
BASE = "http://127.0.0.1:$SIDECAR_PORT"
H = {"X-Sidecar-Token": "$SIDECAR_TOKEN"}
def get(p):
    r = urllib.request.Request(BASE + p, headers=H)
    with urllib.request.urlopen(r, timeout=10) as resp:
        return json.loads(resp.read())
probs = get("/api/v1/problems?limit=1")
print("  GET /problems                  ->", probs.get("total"), "道题")
# 聚合 SQL 走的是 compute_knowledge_stats：方言 SQL 的雷区就在这里，
# 所以画像这两个接口是这个脚本真正要回答的问题。
# （这段 heredoc 不能加引号——要用 shell 展开 SIDECAR_PORT/TOKEN——
#   所以注释里别写反引号，否则会被当成命令执行。）
for path in ("/api/v1/profile/knowledge-stats", "/api/v1/profile/radar"):
    body = get(path)
    print(f"  GET {path:32s} ->", str(body)[:110])
subs = get("/api/v1/submissions?limit=1")
print("  GET /submissions               ->", subs.get("total"), "条提交")
print("  冒烟通过：业务 API 在真实 PostgreSQL 上可用")
PY

if [ "$NO_WEB" = 1 ]; then
  echo ""
  echo "冒烟完成（--no-web）。要浏览器走查就去掉这个参数重跑。"
  exit 0
fi

echo ""
echo "=== 浏览器走查 ==="
echo "  sidecar: http://127.0.0.1:$SIDECAR_PORT（真 PostgreSQL 17.6）"
echo "  前端由下面这条 vite 起，打开它打印的地址（默认 http://localhost:5173）"
echo "  Ctrl+C 结束：会连带停掉 sidecar 与 PostgreSQL"
echo ""
cd "$REPO/desktop" || exit 1
./node_modules/.bin/vite --config vite.config.web.ts --mode web
