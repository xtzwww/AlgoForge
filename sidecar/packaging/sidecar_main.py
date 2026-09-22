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

import contextlib
import os
import sys


def _force_utf8_stdio() -> None:
    """Make the self-check output survive a non-UTF-8 console.

    ⚠️ 2026-09-22 实测踩到：打包版在 Windows 上跑 `--self-check-embedding`
    直接**崩在打印上** ——

        UnicodeEncodeError: 'gbk' codec can't encode character '\\u21b3'

    而不是崩在被检查的东西上。原因是 Windows 控制台默认代码页是 GBK(936)，
    `sys.stdout` 按它编码；中文汉字恰好都在 GBK 里所以一直没暴露，直到输出里
    出现一个 GBK 收不下的字符（这里是 `↳`，还有 `⚠` 也可能）。

    这类故障很难查：**报错指向 `print` 那一行，看起来像被检查对象坏了**，
    而实际上检查逻辑是对的。修法不是"以后别用这个字符"（下一个加符号的人
    还会踩），而是把输出流本身改成 UTF-8，并给 `errors="replace"` 兜底 ——
    宁可少一个装饰字符，也不能让诊断自己把进程带走。

    Only touches TTY/stdout text streams, and never raises: a build that
    cannot reconfigure its stdout should still be able to start serving.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Detached or already-closed streams raise here; the checks below still
        # run, they just lose the ability to print exotic characters.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

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


def self_check_retrieval() -> int:
    """Ask the frozen build's retrieval for real cards; 0 iff it got some.

    Separate from :func:`self_check` on purpose. That one is cheap and
    offline (no model call, no network), so it can run on every build. This
    one **calls the embedding endpoint** — it costs a request and needs the
    network — which is why it is opt-in rather than part of the default pass.

    Why it is worth having at all: the import-level check above proves the
    modules are *present*, not that retrieval *works*. Those are different
    failures with the same symptom ("the analysis never cites any card"):
    a missing module raises ``ImportError``, while a present-but-misconfigured
    embedder makes ``build_retriever`` return a :class:`NullRetriever` that
    searches nothing and raises nothing. Both end in an empty context.
    Only this check can tell them apart.

    It also checks the **data files** the frozen build needs, because that
    was a second, independent failure found the same day: `corpus/` and
    `sidecar/data/` were located through ``__file__``, which under
    PyInstaller points *inside the bundle* — so a packaged build could not
    find its knowledge cards, its problem catalogue, or its vector database,
    while every import-level check passed.
    """
    import sys as _sys
    from pathlib import Path

    if not getattr(_sys, "frozen", False):
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    failed = 0

    # --- data files, cheapest first: no network, and each names its path ---
    try:
        from app.config import DATA_ROOT, settings
        from app.eval_cases import DEFAULT_EVAL_DIR
        from app.problems_corpus import DEFAULT_PROBLEMS_DIR
        from app.rag.documents import DEFAULT_CARDS_DIR
    except Exception as exc:  # noqa: BLE001 — reporting is the whole job
        print(f"FAIL 数据根目录：{type(exc).__name__}: {exc}")
        return 1

    print(f"     DATA_ROOT = {DATA_ROOT}")

    # An embedded Milvus is a file we must be able to open; a Milvus
    # *server* is not a file at all, so there is nothing to stat — only
    # the retrieval below can tell whether it answers.
    lite_path = settings.milvus_lite_path.strip()
    is_server = lite_path.startswith(("http://", "https://"))
    vector_target = None if is_server else Path(lite_path)

    # The three corpus directories are **shipped** — if one is missing the
    # build is wrong. The vector store is **derived** and deliberately not
    # shipped: it is gitignored, produced by `tools/index_knowledge_base.py`
    # or lazily by `_build_index_and_search` on the first retrieval. So a
    # missing store is not a build fault and must not be counted as one —
    # 2026-09-22 实测：全新产物第一次跑这里必然「不存在」，而紧接着的检索
    # 会自己把索引建起来并成功返回 3 张卡片。把「尚未生成」报成 FAIL 会让人
    # 去修一个根本不坏的东西。
    shipped_targets = (
        ("知识卡片", DEFAULT_CARDS_DIR),
        ("题目语料", DEFAULT_PROBLEMS_DIR),
        ("评估集", DEFAULT_EVAL_DIR),
    )
    for label, path in shipped_targets:
        if path.exists():
            print(f"OK   {label:<20} {path}")
        else:
            failed += 1
            print(f"FAIL {label:<20} 不存在：{path}")

    if vector_target is None:
        print(f"OK   向量库                 用 Milvus 服务：{settings.milvus_uri}")
    elif vector_target.exists():
        print(f"OK   向量库                 {vector_target}")
    else:
        print(
            f"WARN 向量库                 {vector_target} 尚未生成"
            "（派生数据，不随包分发；下面的检索会自行建索引）"
        )

    # --- the embedding model: the one setting a packaged build cannot ship ---
    #
    # ⚠️ 2026-09-22：这是打包版检索失败整整一轮的真凶。`.env` 里配的是硅基流动
    # （`EMBEDDING_PROVIDER=openai_compatible`），源码树读得到、一切正常；产物
    # **不带 `.env`**（正确 —— 里面有密钥），于是落回 `config.py` 的默认值
    # `ollama` + `http://127.0.0.1:11434`。本机没开 Ollama，调用时抛出
    # `[WinError 10061] 由于目标计算机积极拒绝，无法连接。`，又被检索的
    # 「Never raise / 失败即空上下文」契约吞成一行 warning —— 界面上只表现为
    # 「分析里没有参考资料」，看不出是配置问题。
    #
    # 所以这里专门查它，并且把「没配」和「配了但连不上」分开报：前者的修法是
    # 设环境变量，后者的修法是去把服务拉起来，两者动作完全不同。
    embedding_ready = False
    provider = (settings.embedding_provider or "").strip().lower()
    if not settings.rag_enabled:
        print("WARN 向量模型                 RAG_ENABLED 关着；检索不启用（这是配置）")
    elif not (settings.embedding_model or "").strip():
        print("WARN 向量模型                 EMBEDDING_MODEL 为空；检索不启用")
    else:
        print(
            f"OK   向量模型                 provider={provider} "
            f"model={settings.embedding_model} base_url={settings.embedding_base_url}"
        )
        if provider == "ollama" and (
            settings.embedding_base_url or ""
        ).strip().startswith(("http://127.0.0.1", "http://localhost")):
            # Not a failure by itself — a machine that really does run a local
            # Ollama is a legitimate deployment. But it is the *default*, so it
            # is also what a packaged build falls back to when nobody set
            # anything, and that is worth saying out loud before the embedder
            # call fails with a bare "connection refused".
            print(
                "      ↳ 这是默认值（本机 Ollama）。打包版不带 .env，所以没显式配置时"
                "就会落到这里。"
            )
            print(
                "        需要另配向量模型时设：EMBEDDING_PROVIDER=openai_compatible "
                "EMBEDDING_BASE_URL=... EMBEDDING_MODEL=... EMBEDDING_API_KEY=..."
            )
        embedding_ready = True

    # --- the real retrieval, which needs the network ---
    query = "二分查找的边界条件"

    try:
        from app.agent.pipeline import _retrieve_context
    except ImportError as exc:
        print(f"FAIL 检索端到端：{type(exc).__name__}: {exc}")
        return 1

    try:
        # ``tags`` empty is the honest call: the real path passes the
        # problem's tags, but this check has no problem — and tags only
        # filter results *after* the vector search, so an empty tuple
        # exercises the same retrieval, just with nothing filtered out.
        context, titles = _retrieve_context(query, "", ())
    except Exception as exc:  # noqa: BLE001 — reporting is the whole job
        print(f"FAIL 检索端到端：{type(exc).__name__}: {exc}")
        return 1

    if not titles:
        # This is the failure the whole check exists for: no exception, no
        # cards, and in production a silent empty context.
        if not embedding_ready:
            print(
                "FAIL 检索端到端：取回 0 张卡片 —— 向量模型没配置（见上面那行 WARN），"
                "检索必然为空。这是配置缺失，不是故障。"
            )
        else:
            print(
                "FAIL 检索端到端：取回 0 张卡片（检索静默降级了 —— 跑一次 "
                "`--self-check-embedding` 看向量模型本身通不通；日志里也可能有 "
                "'rag stack unavailable' / 连接失败）"
            )
        return 1

    print(f"OK   检索端到端：query={query!r} → {len(titles)} 张卡片")
    for title in titles:
        print(f"       - {title}")
    print(f"OK   检索端到端：上下文 {len(context)} 字符")
    return 1 if failed else 0


def self_check_embedding() -> int:
    """Ask the embedding endpoint for one vector.

    The narrowest of the three checks, and the only one that needs nothing
    but the model itself: no vector store, no database, no chat model. It
    exists because "retrieval came back empty" has two unrelated causes and
    the reporting should not make the operator guess which one they have:

    * the embedding endpoint is misconfigured or unreachable — the fix is a
      setting or a running service;
    * the endpoint is fine and the vector store is empty or broken — the
      fix is an index rebuild.

    Returns 0 when a vector came back, 1 otherwise.
    """
    import sys as _sys
    from pathlib import Path

    if not getattr(_sys, "frozen", False):
        _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    try:
        from app.config import settings
        from app.rag.embedder import build_embedder
    except Exception as exc:  # noqa: BLE001 — reporting is the whole job
        print(f"FAIL 向量模型装配：{type(exc).__name__}: {exc}")
        return 1

    print(
        f"     provider={settings.embedding_provider!r} "
        f"model={settings.embedding_model!r} "
        f"base_url={settings.embedding_base_url!r}"
    )
    if settings.embedding_api_key:
        # Never print the key; just confirm one is present, because "the key
        # is missing" and "the key is wrong" look identical in a 401.
        key_len = len(settings.embedding_api_key)
        print(f"     EMBEDDING_API_KEY: 已设置（{key_len} 字符）")
    else:
        print("     EMBEDDING_API_KEY: 未设置")

    embedder = build_embedder()
    if embedder is None:
        print("FAIL 向量模型：build_embedder() 返回 None —— 上面有空的项")
        return 1

    try:
        vector = embedder("二分查找")
    except Exception as exc:  # noqa: BLE001 — the exception IS the answer here
        print(f"FAIL 向量模型：调用失败 {type(exc).__name__}: {exc}")
        # Matched by name rather than by `except httpx.ConnectError`: httpx is
        # not imported by this module, and more importantly the embedder has
        # two implementations (Ollama and OpenAI-compatible) whose transport
        # errors do not share a base class. `ConnectError` is httpx's;
        # `ConnectionError` / `OSError` are what a raw socket gives.
        #
        # `[WinError 10061] 由于目标计算机积极拒绝` / `Connection refused` is
        # the signature of "127.0.0.1:<port> has nobody listening" — which in
        # this project almost always means the default Ollama endpoint, not a
        # network problem. Saying so here saves the next person the same hour
        # it cost on 2026-09-22.
        name = type(exc).__name__
        refused = isinstance(exc, (ConnectionError, OSError)) or name in {
            "ConnectError",
            "ConnectTimeout",
            "NewConnectionError",
        }
        if refused:
            print(
                "     ↳ 连不上端点。若 base_url 是 127.0.0.1，说明它指向本机服务"
                "（默认值是 Ollama）而该服务没在跑；改用云端端点请设 "
                "EMBEDDING_PROVIDER=openai_compatible + EMBEDDING_BASE_URL。"
            )
        return 1

    try:
        size = len(list(vector))
    except TypeError:
        print(f"FAIL 向量模型：返回的东西不能转成向量（{type(vector).__name__}）")
        return 1

    print(f"OK   向量模型：'二分查找' → {size} 维向量")
    if size == 0:
        print("FAIL 向量模型：维度是 0")
        return 1
    return 0


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

    # Opt-in on top of `--self-check`: same reason for substring matching, and
    # kept separate because it calls the embedding endpoint (costs a request).
    if "--self-check-retrieval" in sys.argv[1:]:
        raise SystemExit(self_check_retrieval())

    # The narrowest of the three: only the embedding endpoint. Exists because
    # "retrieval returned nothing" has two very different causes — the model
    # endpoint is unreachable, or the vector store is empty/broken — and this
    # separates them without needing a database or a chat model.
    if "--self-check-embedding" in sys.argv[1:]:
        raise SystemExit(self_check_embedding())

    # Opt-in on top of `--self-check`: same reason for substring matching, and
    # kept separate because it calls the embedding endpoint (costs a request).
    if "--self-check-retrieval" in sys.argv[1:]:
        raise SystemExit(self_check_retrieval())

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
