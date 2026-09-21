#!/usr/bin/env bash
# 手动走查启动：绕开 PostgreSQL，用 SQLite 起 sidecar + 网页版前端。
#
# 背景（2026-09-19 更新）：以前这么绕是因为 PG 连不上，但**那是个误判**——
# 真正原因是 PG 不跨 shell 调用存活 + stale postmaster.pid，不是网络。真 PG
# 现在能用，见 `start-walkthrough-pg.sh`（那份连的是真实 PostgreSQL 17.6）。
#
# 这份仍有用：起得更快、不依赖 D:\pg，且 SQLite 下能跑通的东西不代表真 PG
# 下能跑（SQLite 默认不强制外键、函数名也不同）——所以**两个都值得跑**。
#
# 核心启动命令已于 2026-09-19 实测通过：
#   /health                     -> {"status":"ok"}
#   带 X-Sidecar-Token 的业务接口 -> 200
#   不带 token                   -> 401
#
# 用法：bash start-walkthrough.sh
#   然后打开浏览器访问 vite 打印的地址（默认 http://localhost:5173）
# 停止：Ctrl+C（会连带杀掉 sidecar）
#
# 对应的前端配置在 desktop/.env.web：
#   VITE_SIDECAR_URL=http://127.0.0.1:8123
#   VITE_SIDECAR_TOKEN=dev-local-token   ← 必须与下面的 SIDECAR_TOKEN 一致
#
# ⚠️ 绝不要用 `env` 来设或清环境变量（2026-09-20 实测，踩过）
# ------------------------------------------------------------
# 本机 `~/.local/bin/env` 覆盖了 `/usr/bin/env`，而它是个 328 字节的空壳脚本：
# **忽略全部参数、什么都不执行、还返回 0**。所以下面这两种写法都是彻底的空操作：
#
#   env -u PYTHONPATH FOO=1 cmd     # 清 + 设，整条被吞
#   env FOO=1 cmd                   # 只设，同样被吞
#
# 特征正是「**零输出 + rc=0**」，`set -e` 抓不住。本脚本原先就是 `env -u` 形式，
# 于是 sidecar 从来没起过——而脚本只会在「等 /health」空转 40 秒后才暴露，
# 更糟的情况是复用上一次残留的 sidecar 假装成功。
#
# 正确写法：子 shell 里 `unset` 再 `export`，最后 `exec`。**`exec` 不换 PID**，
# 所以下面 `$!` 拿到的仍是 uvicorn 自己的进程，EXIT trap 的 `kill` 依旧有效
# （这个脚本不能用 `taskkill`：PID 来自 `$!`，是 MSYS 命名空间里的真 PID）。

cd /d/BaiduNetdiskDownload/AlgoForge/sidecar || exit 1

die() { echo "[walkthrough] 失败：$*" >&2; exit 1; }

(
  unset PYTHONPATH
  export DATABASE_URL="sqlite:///C:/Windows/Temp/af_dev.db"
  export SIDECAR_PORT=8123
  export SIDECAR_TOKEN=dev-local-token
  exec .venv/Scripts/python.exe -u -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8123
) > C:/Windows/Temp/uv2.log 2>&1 &
SIDECAR_PID=$!
trap 'kill $SIDECAR_PID 2>/dev/null' EXIT

echo "[sidecar] starting on :8123 (SQLite), waiting for /health ..."
ready=no
for i in $(seq 1 20); do
  if curl -sf --noproxy "*" -m 2 http://127.0.0.1:8123/health > /dev/null 2>&1; then
    echo "[sidecar] ready after $((i * 2))s"
    ready=yes
    break
  fi
  sleep 2
done

# 起不来就**别往下走**（2026-09-21 改）。
#
# 原先这里直接去起 vite，于是演示时得到的是「页面能打开、接口全挂」——而那种
# 状态最容易被当成前端 bug 去查，实际上 sidecar 根本没起来。宁可现在明确失败：
# 一个报错的启动脚本比一个看起来正常的坏页面便宜得多。
#
# `exit 1` 会触发上面的 EXIT trap，把那个半死不活的 sidecar 一起收掉。
[ "$ready" = yes ] || die "sidecar 40 秒内没有响应 /health；日志见 C:/Windows/Temp/uv2.log"

cd /d/BaiduNetdiskDownload/AlgoForge/desktop || exit 1
echo "[web] vite dev — 浏览器打不开时先怀疑代理：用 127.0.0.1 并让代理绕过本机"
./node_modules/.bin/vite --config vite.config.web.ts --mode web
