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

Build it (see `packaging/build_sidecar.sh`):

    pyinstaller --onedir --name algoforge-sidecar \\
        --add-data "app/prompts/*.md;app/prompts" packaging/sidecar_main.py
"""

from __future__ import annotations

import os
import sys


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
