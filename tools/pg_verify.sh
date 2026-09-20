#!/usr/bin/env bash
# ============================================================
# 在**真实 PostgreSQL** 上跑迁移 + 聚合 SQL。
#
#   ./tools/pg_verify.sh
#
# 为什么需要这个脚本
# ------------------
# 项目反复踩的坑是「方言 SQL」：`compute_knowledge_stats` 和题库的知识点
# 筛选曾经用 `func.json_each`，那是 SQLite 的函数名。整套测试跑在 SQLite 上
# （conftest 把 get_session 换成内存库），所以这类 bug 在本地**永远不会红**。
#
# 本机 2026-09-19 之前无法验证：没有 PG，Docker 路线也不通（`wsl.exe` 被
# 安全策略拉黑）。现在 `D:\pg` 有一份 zip 免安装版 PostgreSQL 17.6，本脚本
# 就是把它跑起来并做验证的最短路径。
#
# 三个必须知道的点（每一个都曾让这一步失败）
# ----------------------------------------
# 1. **PG 不跨 shell 调用存活。** 沙箱在每次调用结束时杀掉进程树，所以
#    「启动」和「使用」必须在**同一次调用**里完成——本脚本就是一次调用。
#    历史上「连不上 PG」的真正原因不是网络，而是 PG 根本没在跑。
# 2. **必须清理 stale `postmaster.pid`。** 上一次被强杀会留下它，pg_ctl 因此
#    认为服务器仍在运行、拒绝启动，且日志是空的（极具误导性）。删除前本脚本
#    会先确认没有 postgres 进程和 5432 监听，避免双写损坏数据目录。
# 3. **崩溃恢复需要时间。** 强杀后首次启动会 `syncing data directory`，实测
#    可长达 40 秒，期间 `pg_isready` 返回 "rejecting connections"。所以必须
#    **轮询**而不是睡固定时长。脚本末尾用 `-m fast` 干净停止，下次启动就只要
#    2 秒——请让它跑到最后，别中途 Ctrl-C。
#
# 另外：HKLM AutoRun 被设成 `chcp 65001`，任何经 cmd.exe 启动的进程都会在
# stdout 多打一行，而 PG 用 popen 读 `postgres -V` 的**第一行**做自校验，于是
# initdb / postgres / pg_ctl 会拒绝启动（"could not locate matching postgres
# executable"）。`D:\pg\nocmd.exe` 是替代 COMSPEC 的小包装器（直接 spawn 目标
# 程序，不经 cmd.exe）。改 HKLM 要管理员，本机没有，所以只能这样。
# 注意：**只对本命令设置 COMSPEC，不要 export**，否则 Bash 自己也找不到 shell。
#
# ⚠️ 4. **绝不要用 `env` 来设/清环境变量。** 本机 `~/.local/bin/env` 覆盖了
#    `/usr/bin/env`，而它是个空壳脚本——**忽略全部参数、什么都不执行、还返回 0**。
#    所以下面两种写法都是**彻底的空操作**（命令一个字没跑，rc 却是 0，静默假绿）：
#        env -u PYTHONPATH python x.py        # 没清 PYTHONPATH，也没跑 python
#        env COMSPEC=... postgres.exe ...     # PG 根本没启动
#    2026-09-20 实测：本脚本曾同时被这两条打穿——PG 起不来、后面的 python 也没跑，
#    而脚本会一路打印到「PG 未就绪」才失败（或更糟：复用上一次残留的 PG 假装成功）。
#    正确写法：
#      · 清变量 → 子 shell 里 `unset` 再 `exec`（见下面的 `py()`）
#      · 设变量 → 直接前缀赋值（`COMSPEC=... cmd`，只作用于该命令，等价于原意）
# ============================================================
set -uo pipefail

PG_HOME="${PG_HOME:-D:/pg}"
PG_DATA="$PG_HOME/data"
PG_BIN="$PG_HOME/pgsql/bin"
NOCMD="$PG_HOME/nocmd.exe"
PG_URL="postgresql://algoforge:algoforge@127.0.0.1:5432"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "[pg_verify] 失败：$*" >&2; exit 1; }

# 跑 Python 的唯一入口：子 shell 里清掉 PYTHONPATH 再 exec。
# 不用 `env -u PYTHONPATH` —— 理由见文件头第 4 条（本机 env 是空壳，会假绿）。
PY="$REPO/sidecar/.venv/Scripts/python.exe"
py() { ( unset PYTHONPATH; exec "$PY" "$@" ); }

[ -x "$NOCMD" ] || [ -f "$NOCMD" ] || die "缺少 $NOCMD（PG 自校验绕法，别删）"
[ -f "$PG_DATA/PG_VERSION" ] || die "$PG_DATA 未初始化（没有 PG_VERSION），先跑 initdb"

# --- 1. 清理 stale postmaster.pid（先确认服务器确实没在跑） ---------------
if [ -f "$PG_DATA/postmaster.pid" ]; then
  if tasklist 2>/dev/null | grep -qi postgres; then
    echo "[pg_verify] 已有 postgres 进程在跑，直接复用，不删 pid 文件"
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
    > "$PG_HOME/pg_verify.log" 2>&1 &)

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
[ "$ready" = yes ] || { tail -15 "$PG_HOME/pg_verify.log"; die "PG 未就绪，看上面日志"; }

# --- 4. 建库（幂等） ------------------------------------------------------
echo "[4/6] 确保库 algoforge 存在"
py -c "
import psycopg
with psycopg.connect('$PG_URL/postgres') as c:
    c.autocommit = True
    if not c.execute(\"select 1 from pg_database where datname='algoforge'\").fetchone():
        c.execute('create database algoforge'); print('        已建库')
    else:
        print('        库已存在')
" || die "建库失败"

# --- 5. 迁移（真 PG，不是 SQLite） ---------------------------------------
echo "[5/6] alembic upgrade head（真实 PostgreSQL）"
(cd "$REPO/sidecar" && unset PYTHONPATH && exec ./.venv/Scripts/python.exe -m alembic \
    -x "db_url=postgresql+psycopg://algoforge:algoforge@127.0.0.1:5432/algoforge" \
    upgrade head) || die "迁移失败"

# --- 6. 聚合 SQL ---------------------------------------------------------
echo "[6/6] 聚合 SQL 测试（真实 PostgreSQL）"
(cd "$REPO" && unset PYTHONPATH && export ALGOFORGE_PG_TEST_URL="postgresql+psycopg://algoforge:algoforge@127.0.0.1:5432/algoforge" && \
    exec sidecar/.venv/Scripts/python.exe -u -m pytest tests/test_postgres_aggregation.py -v -m "not eval_set")
STATUS=$?

# --- 收尾：干净停止，下次启动才不用崩溃恢复 -------------------------------
echo "[收尾] 停止 PostgreSQL"
(cd "$PG_HOME" && COMSPEC="$NOCMD" "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -m fast stop 2>&1 | tail -1)

exit "$STATUS"
