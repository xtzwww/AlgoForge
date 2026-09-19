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

cd /d/BaiduNetdiskDownload/AlgoForge/sidecar || exit 1

env -u PYTHONPATH DATABASE_URL="sqlite:///C:/Windows/Temp/af_dev.db" SIDECAR_PORT=8123 SIDECAR_TOKEN=dev-local-token .venv/Scripts/python.exe -u -m uvicorn app.main:app --host 127.0.0.1 --port 8123 > C:/Windows/Temp/uv2.log 2>&1 &
SIDECAR_PID=$!
trap 'kill $SIDECAR_PID 2>/dev/null' EXIT

echo "[sidecar] starting on :8123 (SQLite), waiting for /health ..."
for i in $(seq 1 20); do
  if curl -sf --noproxy "*" -m 2 http://127.0.0.1:8123/health > /dev/null 2>&1; then
    echo "[sidecar] ready after $((i * 2))s"
    break
  fi
  sleep 2
done

cd /d/BaiduNetdiskDownload/AlgoForge/desktop || exit 1
echo "[web] vite dev — 浏览器打不开时先怀疑代理：用 127.0.0.1 并让代理绕过本机"
./node_modules/.bin/vite --config vite.config.web.ts --mode web
