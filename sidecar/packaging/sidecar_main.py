"""PyInstaller entry point for the sidecar (阶段 7 · 打包).

Why this file exists at all
---------------------------
Development starts the sidecar as `python -m uvicorn app.main:app ...`.
PyInstaller cannot freeze a `-m` invocation — it needs a script whose
imports it can follow. So this is that script, and it keeps the same
contract the Electron main process already depends on (see
`desktop/src/main/sidecar.ts`): the port and the token arrive as
`--port` and as environment variables, and `/health` is how the caller
knows the process is up.

Two things the frozen build does differently, both on purpose:

* **No `.env` inside the bundle.** `app.config` anchors the env file next
  to the package (`sidecar/.env`), which under PyInstaller resolves to a
  path inside the extracted temp dir — a place that gets wiped. Rather
  than copying secrets into the bundle (§六 阶段 7 完成定义第 4 条 forbids
  exactly that), the packaged build reads either real environment
  variables or a file named by `ALGOFORGE_ENV_FILE`.

* **`--host` / `--port` are parsed here, not by uvicorn's CLI**, because
  there is no CLI in the frozen build: `uvicorn.run()` is called
  in-process. Keeping the flag spellings identical to uvicorn's means
  the spawn site in `sidecar.ts` only has to change the program it
  launches, not the arguments it passes.

Run the frozen build:

    algoforge-sidecar.exe --host 127.0.0.1 --port 8123

Check that the frozen build can still import the whole analysis stack:

    algoforge-sidecar.exe --self-check

Build it (see `packaging/build_sidecar.sh`):

    pyinstaller --onedir --name algoforge-sidecar \\
        --add-data "app/prompts/*.md;app/prompts" packaging/sidecar_main.py
"""

from __future__ import annotations

import os
import sys

#: Modules the frozen build must be able to import, but that **nothing imports
#: at startup** — so a frozen build can pass `/health` and every judge test
#: while still being unable to run an analysis. Each entry is
#: ``(label, module)``.
#:
#: Why this list exists at all: these are reached through imports inside
#: functions (`pipeline.py` only imports the graph on the `graph`/`team`
#: branches, `retriever.py` only imports `pymilvus` when it really connects,
#: `memory.py` only imports the PG backends when a PG URI is configured).
#: PyInstaller does follow in-function imports, so in practice they *are*
#: bundled — but that is incidental, and the failure mode when it stops being
#: true is "green at build time, ImportError in production". Declaring them in
#: `build_sidecar.sh` turns it into a build-time contract; this list is the
#: runtime check that the contract held.
_SELF_CHECK_MODULES = (
    ("S4 编排图", "app.agent.graph"),
    ("S7 三智能体", "app.agent.team"),
    ("S5 检索层", "app.rag.retriever"),
    # 2026-09-22 补：这两个是「把文字变成向量」与「两遍检索的第二遍」的所在。
    # 它们不是懒导入 —— `app/rag/__init__.py` 在**顶层**就 re-export 它们，
    # 而 `pipeline.py` 的 `from app.rag import build_embedder` 又包在
    # `try/except ImportError` 里。三者叠加的后果：包体缺这两个模块时，冻结版
    # 的检索**静默关闭**（一行 info 日志，不报错，界面也看不出来），而只查
    # `app.rag.retriever` 的自检照样全绿。实测踩到过一次（产物早于模块）。
    ("S5 向量模型", "app.rag.embedder"),
    ("S5 重排器", "app.rag.reranker"),
    ("S5 Milvus 客户端", "pymilvus"),
    ("S8 短期记忆（PG）", "langgraph.checkpoint.postgres"),
    ("S8 长期记忆（PG）", "langgraph.store.postgres"),
)


def self_check() -> int:
    """Import the lazy analysis stack and report per module; 0 iff all pass.

    Imports only — no model call, no database, no Milvus connection. That is
    the point: it can run in CI and in a packaged smoke test, and it fails for
    exactly one reason (a module missing from the bundle) rather than for the
    many reasons a real analysis can fail.

    It also *builds* the graph, because importing `app.agent.graph` does not
    exercise langgraph's `StateGraph` API — a missing langgraph submodule shows
    up when the graph is assembled, not when the module is imported.
    """
    import importlib
    from pathlib import Path

    if not getattr(sys, "frozen", False):
        # Source-tree run: this file lives in `packaging/`, so Python puts that
        # on `sys.path` — not the `sidecar/` dir that actually holds the `app`
        # package. The frozen build has `app` in its archive, so this branch is
        # only ever taken by the "check before you build" run, which is worth
        # supporting: it separates "the venv is broken" from "the bundle is
        # broken" instead of blaming the bundle for both.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    failed = 0
    for label, name in _SELF_CHECK_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — reporting is the whole job
            failed += 1
            print(f"FAIL {label:<20} {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"OK   {label:<20} {name}")

    # `build_analysis_graph()` is all-optional, and with no retriever it builds
    # its own — which is exactly the code path a frozen `graph` deployment
    # takes, so it is worth the few milliseconds.
    graph_step = "构建分析图"
    try:
        from app.agent.graph import build_analysis_graph

        build_analysis_graph()
    except Exception as exc:  # noqa: BLE001
        failed += 1
        print(f"FAIL {graph_step:<20} {type(exc).__name__}: {exc}")
    else:
        print(f"OK   {graph_step:<20} build_analysis_graph()")

    # 按**真实调用方式**装配检索，而不是只 import 模块。`pipeline.py` 用的是
    # `from app.rag import build_embedder, ...`，而 `app/rag/__init__.py` 顶层
    # re-export 了 embedder 与 reranker —— 缺任何一个，这里的 ImportError 就
    # 是线上那份静默降级。只 import `app.rag.retriever` 是抓不到的。
    retrieval_step = "S5 检索装配"
    try:
        from app.rag import (
            build_embedder,
            build_reranker,
            build_retriever,
        )
    except Exception as exc:  # noqa: BLE001 — reporting is the whole job
        failed += 1
        print(f"FAIL {retrieval_step:<20} {type(exc).__name__}: {exc}")
    else:
        # 只取对象，不调用 —— 调用会去连向量模型端点，那是真分析的事。
        assert callable(build_embedder)
        assert callable(build_reranker)
        assert callable(build_retriever)
        print(f"OK   {retrieval_step:<20} app.rag.build_embedder/reranker/retriever")

    total = len(_SELF_CHECK_MODULES) + 2
    print(f"self-check: {total - failed}/{total} 通过")
    return 1 if failed else 0


def parse_args(argv: list[str]) -> tuple[str, int]:
    """Pull `--host` / `--port` out of `argv`, uvicorn-style.

    Unknown flags are ignored rather than rejected: the Electron caller
    passes only these two today, but a stricter parser would turn any
    future flag into a startup failure, and a sidecar that refuses to
    start is worse than one that ignores an argument it does not know.
    """
    host = "127.0.0.1"
    port = 0
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
        elif arg == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
        else:
            i += 1
    return host, port


def main() -> None:
    # Checked before anything else, and by substring rather than through
    # `parse_args`: that parser deliberately ignores unknown flags, so routing
    # this through it would mean either teaching it a flag the Electron caller
    # must never pass, or changing the "ignore what you do not know" contract
    # the two-process handshake depends on.
    if "--self-check" in sys.argv[1:]:
        raise SystemExit(self_check())

    # Imported here rather than at module scope so `--help`-style misuse
    # and import errors surface as the real traceback, and so PyInstaller
    # still sees them (it follows imports inside functions too).
    import uvicorn

    from app.main import app

    host, port = parse_args(sys.argv[1:])
    # The env var is the documented way to set it (`SIDECAR_PORT=`); the
    # flag is what the Electron caller passes. Flag wins: it names the
    # port the caller already reserved.
    if not port:
        port = int(os.environ.get("SIDECAR_PORT") or 0)

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
