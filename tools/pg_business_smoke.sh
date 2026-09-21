#!/usr/bin/env bash
# ============================================================
# 真实 PostgreSQL 上的完整业务链路冒烟（队列 T26）。
#
#   bash tools/pg_business_smoke.sh
#
# 一次调用里做完：起 PG → 建**临时库** → alembic 迁移 → 跑
# tests/test_pg_business_flow.py → 干净停库。
#
# 为什么不复用 tools/pg_verify.sh
# ------------------------------
# 两者只差一件要紧的事：**本脚本不碰应用在用的 algoforge 库**。
# pg_verify.sh 的最后一个步骤会在 algoforge 上跑聚合测试，而那个测试按设计
# **清空所有表**——对着开发库跑等于删掉用户的数据。所以这里另建一个
# algoforge_smoke，跑完就留在那儿（几十 KB），任何时候都能重跑。
#
# PG 的三个坑（每一个都曾让这一步失败，详见 tools/pg_verify.sh 的文件头）
# ------------------------------------------------------------------
# 1. **PG 不跨 shell 调用存活**——沙箱在每次调用结束时杀进程树。所以启动与
#    使用必须在同一次调用里，本脚本就是那一次。
# 2. **必须清理 stale `postmaster.pid`**，否则 pg_ctl 认为服务器还在跑、
#    拒绝启动，而日志是空的（极具误导性）。删之前先确认没有进程、没有监听。
# 3. **崩溃恢复要时间**（硬杀后实测可达 40 秒），所以轮询 `pg_isready`，
#    不睡固定时长。末尾用 `-m fast` 干净停止，下次启动才只要 2 秒。
#
# 还有两个本机特有的：HKLM AutoRun 的 `chcp 65001` 会让经 cmd.exe 启动的
# 进程多打一行，PG 读 `postgres -V` 第一行做自校验就会拒绝启动——用
# `D:\pg\nocmd.exe` 当 COMSPEC 绕开（只对本命令设，**不要 export**）。
# 以及 ⚠️ **绝不要用 `env` 来设/清环境变量**：本机 `~/.local/bin/env` 是空壳
# 脚本，忽略全部参数、什么都不执行、还返回 0（静默假绿）。清变量用子 shell 里
# `unset` 再 `exec`，设变量用前缀赋值。
# ============================================================
set -uo pipefail

PG_HOME="${PG_HOME:-D:/pg}"
PG_DATA="$PG_HOME/data"
PG_BIN="$PG_HOME/pgsql/bin"
NOCMD="$PG_HOME/nocmd.exe"
PG_SUPER="postgresql://algoforge:algoforge@127.0.0.1:5432"
SMOKE_DB="${SMOKE_DB:-algoforge_smoke}"
SMOKE_URL="postgresql+psycopg://algoforge:algoforge@127.0.0.1:5432/$SMOKE_DB"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "[pg_smoke] 失败：$*" >&2; exit 1; }

# 跑 Python 的唯一入口：子 shell 里清掉 PYTHONPATH 再 exec。
# 不用 `env -u PYTHONPATH` —— 理由见文件头（本机 env 是空壳，会假绿）。
PY="$REPO/sidecar/.venv/Scripts/python.exe"
py() { ( unset PYTHONPATH; exec "$PY" "$@" ); }

[ -x "$NOCMD" ] || [ -f "$NOCMD" ] || die "缺少 $NOCMD（PG 自校验绕法，别删）"
[ -f "$PG_DATA/PG_VERSION" ] || die "$PG_DATA 未初始化（没有 PG_VERSION），先跑 initdb"

STATUS=0
stopped=no
stop_pg() {
  [ "$stopped" = yes ] && return
  stopped=yes
  echo "[收尾] 干净停止 PostgreSQL（下次启动才不用崩溃恢复）"
  (cd "$PG_HOME" && COMSPEC="$NOCMD" "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -m fast stop 2>&1 | tail -1)
}
# 无论怎么退出都要停库，否则下一次启动要等几十秒的 fsync 恢复。
trap stop_pg EXIT

# --- 1. 清理 stale postmaster.pid（先确认服务器确实没在跑） ---------------
if [ -f "$PG_DATA/postmaster.pid" ]; then
  if tasklist 2>/dev/null | grep -qi postgres; then
    echo "[1/6] 已有 postgres 进程在跑，直接复用，不删 pid 文件"
  elif netstat -ano 2>/dev/null | grep -q "127.0.0.1:5432 .*LISTENING"; then
    die "5432 已被监听但没有 postgres 进程——先查清是谁占用，不要盲删 pid"
  else
    echo "[1/6] 删除 stale postmaster.pid（已确认无进程、无监听）"
    rm -f "$PG_DATA/postmaster.pid"
  fi
else
  echo "[1/6] 无 postmaster.pid"
fi

# --- 2. 启动 -------------------------------------------------------------
echo "[2/6] 启动 PostgreSQL（后台，不跨调用存活）"
(cd "$PG_HOME" && COMSPEC="$NOCMD" "$PG_BIN/postgres.exe" -D "$PG_DATA" \
    > "$PG_HOME/pg_smoke.log" 2>&1 &)

# --- 3. 轮询等待就绪（崩溃恢复可能要 40 秒） ------------------------------
ready=no
for i in $(seq 1 40); do
  sleep 2
  if "$PG_BIN/pg_isready.exe" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "[3/6] 就绪（$((i * 2)) 秒）"
    ready=yes
    break
  fi
done
[ "$ready" = yes ] || { tail -15 "$PG_HOME/pg_smoke.log"; die "PG 未就绪，看上面日志"; }

# --- 4. 建**临时库**（幂等；绝不碰 algoforge） ---------------------------
echo "[4/6] 确保临时库 $SMOKE_DB 存在（不碰应用在用的 algoforge）"
py -c "
import psycopg
with psycopg.connect('$PG_SUPER/postgres') as c:
    c.autocommit = True
    if not c.execute(\"select 1 from pg_database where datname='$SMOKE_DB'\").fetchone():
        c.execute('create database $SMOKE_DB'); print('        已建库')
    else:
        print('        库已存在')
" || die "建库失败"

# --- 5. 迁移（真 PG，不是 SQLite） ---------------------------------------
echo "[5/6] alembic upgrade head（临时库）"
(cd "$REPO/sidecar" && unset PYTHONPATH && exec ./.venv/Scripts/python.exe -m alembic \
    -x "db_url=$SMOKE_URL" \
    upgrade head) || die "迁移失败"

# --- 6. 业务链路冒烟 -----------------------------------------------------
echo "[6/6] 六项功能的接口级冒烟（真实 PostgreSQL）"
(cd "$REPO" && unset PYTHONPATH && export ALGOFORGE_PG_TEST_URL="$SMOKE_URL" && \
    exec sidecar/.venv/Scripts/python.exe -u -m pytest \
        tests/test_pg_business_flow.py -v -m "not eval_set")
STATUS=$?

exit "$STATUS"
