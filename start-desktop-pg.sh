#!/usr/bin/env bash
# ============================================================
# 启动桌面版（Electron）+ 真实 PostgreSQL。
#
#   bash start-desktop-pg.sh
#
# 为什么必须有这个脚本
# --------------------
# `sidecar/.env` 的 DATABASE_URL 指向 `localhost:5432` 的 PostgreSQL，而
# PostgreSQL **不会自己启动**。后果非常隐蔽：
#
#   * `/health` 不碰数据库 → 返回 200 → 主进程认为引擎就绪，正常开窗；
#   * 题库 / 错题本 / 教师端这些**要查库**的接口 → 连不上 5432 → **挂住**；
#   * 前端等 30 秒超时，或连接被重置 → 页面报 **「Network Error」**。
#
# 于是现象是「引擎看起来是好的，但每一页都报错」，而日志里没有任何异常。
# 2026-09-20 晚上实际发生过一次，排查花了不少时间才定位到 5432 没在监听。
#
# ✅ **2026-09-20 后续：应用已经会自己起 PG 了**（`desktop/src/main/pg.ts`，
# dev 模式下在 spawn sidecar 前确保 5432 可用）。所以**本脚本现在是可选的**：
# 直接 `electron-vite dev` 就行。
#
# 留着它有两个理由：① 先把 PG 起好，应用启动就不用等（尤其刚硬杀过、要跑
# fsync 恢复的时候）；② 你 Ctrl+C 之后它保证把 PG 干净停掉，下次启动才是
# 2 秒而不是几十秒。
#
# ⚠️ **PG 不能由自动化会话代为启动。** 沙箱会在每次 Bash 调用结束时杀掉
# 进程树，所以从脚本外起的 PG 活不过那一次调用（实测过：同一调用内
# `pg_isready` 正常，下一次调用就 `no response`）。必须由**你自己的终端**
# 来起——本脚本就是给你在 Git Bash 里跑的。
#
# 三个坑（都是从 `tools/pg_verify.sh` 继承来的，每一个都曾让这步失败）
# ------------------------------------------------------------------
# 1. **必须清理 stale `postmaster.pid`。** 上次被强杀会留下它，pg_ctl 因此
#    认为服务器还在跑、拒绝启动，且日志是空的。删除前先确认没有 postgres
#    进程、5432 没有监听，避免双写损坏数据目录。
# 2. **崩溃恢复要时间。** 强杀后首次启动会 `syncing data directory`，实测可
#    长达 40 秒，期间 `pg_isready` 返回 "rejecting connections"。所以必须
#    **轮询**，不能睡固定时长。脚本收尾用 `-m fast` 干净停止，下次就只要
#    2 秒 —— 请用 Ctrl+C 正常退出，别直接关窗口。
# 3. **`COMSPEC` 必须换成 `nocmd.exe`。** HKLM AutoRun 被设成 `chcp 65001`，
#    任何经 cmd.exe 启动的进程 stdout 都会多打一行，而 PG 用 popen 读
#    `postgres -V` 的**第一行**做自校验，于是拒绝启动（"could not locate
#    matching postgres executable"）。改 HKLM 要管理员，本机没有，所以
#    用 `D:\pg\nocmd.exe` 这个不经 cmd.exe 的小包装器代替。
#
# ⚠️ 4. **绝不要用 `env` 设/清环境变量。** 本机 `~/.local/bin/env` 覆盖了
#    `/usr/bin/env`，而它是个**空壳脚本**：忽略全部参数、什么都不执行、还
#    返回 0。所以 `env COMSPEC=... postgres.exe ...` 是彻底的空操作 —— PG
#    根本没起，rc 却是 0（静默假绿）。正确写法就是下面的**前缀赋值**。
# ============================================================
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PG_HOME="${PG_HOME:-D:/pg}"
PG_DATA="$PG_HOME/data"
PG_BIN="$PG_HOME/pgsql/bin"
NOCMD="$PG_HOME/nocmd.exe"

# 跑 Python 的唯一入口：子 shell 里清掉 PYTHONPATH 再 exec。
PY="$REPO/sidecar/.venv/Scripts/python.exe"
py() { ( unset PYTHONPATH; exec "$PY" "$@" ); }

die() { echo "[desktop-pg] 失败：$*" >&2; exit 1; }

[ -f "$NOCMD" ] || die "缺少 $NOCMD（PG 自校验绕法，别删）"
[ -f "$PG_DATA/PG_VERSION" ] || die "$PG_DATA 未初始化（没有 PG_VERSION），先跑 initdb"

# --- 0. 已经在跑就复用（不抢别人的 PG，收尾也不去关它） ------------------
PG_STARTED_BY_US=0
if netstat -ano 2>/dev/null | grep -q "127.0.0.1:5432 .*LISTENING"; then
  echo "[0/3] 5432 已在监听，复用现有 PostgreSQL（收尾时不会停它）"
else
  # --- 1. 清理 stale pid -------------------------------------------------
  if [ -f "$PG_DATA/postmaster.pid" ]; then
    if tasklist 2>/dev/null | grep -qi postgres; then
      echo "[1/3] 有 postgres 进程在跑，复用，不删 pid 文件"
    else
      echo "[1/3] 删除 stale postmaster.pid（已确认无进程、无监听）"
      rm -f "$PG_DATA/postmaster.pid"
    fi
  else
    echo "[1/3] 无 postmaster.pid"
  fi

  # --- 2. 启动（后台，但活在本终端的会话里，不会随单次调用消失） --------
  echo "[2/3] 启动 PostgreSQL（崩溃恢复可能要 40 秒，轮询等待）"
  (cd "$PG_HOME" && COMSPEC="$NOCMD" "$PG_BIN/postgres.exe" -D "$PG_DATA" \
      > "$PG_HOME/pg_desktop.log" 2>&1 &)
  PG_STARTED_BY_US=1

  ready=no
  for i in $(seq 1 40); do
    sleep 2
    if "$PG_BIN/pg_isready.exe" -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
      echo "      就绪（$((i * 2)) 秒）"; ready=yes; break
    fi
  done
  [ "$ready" = yes ] || { tail -15 "$PG_HOME/pg_desktop.log"; die "PG 未就绪，看上面日志"; }
fi

# 收尾：只停我们自己起的 PG，且用 -m fast 干净停止（下次启动才不用崩溃恢复）。
cleanup() {
  if [ "$PG_STARTED_BY_US" = 1 ]; then
    echo ""
    echo "[收尾] 停止 PostgreSQL（-m fast，干净停止）"
    (cd "$PG_HOME" && COMSPEC="$NOCMD" "$PG_BIN/pg_ctl.exe" -D "$PG_DATA" -m fast stop \
        2>&1 | tail -1)
  fi
}
trap cleanup EXIT

# --- 3. 起桌面版 ---------------------------------------------------------
#
# 直接调 `./node_modules/.bin/*`，**不用 `npm run`**：本机 `npm run` 会静默
# exit 1（见项目记忆），排查时极具误导性。
echo "[3/3] 启动 Electron（electron-vite dev）"
echo "      Ctrl+C 退出：PG 会被干净停止，下次启动只要 2 秒。"
echo ""
cd "$REPO/desktop" || die "找不到 desktop/"
exec ./node_modules/.bin/electron-vite dev
