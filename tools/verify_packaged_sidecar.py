#!/usr/bin/env python
"""Verify the PACKAGED sidecar end to end, per language.

    sidecar/.venv/Scripts/python.exe -u tools/verify_packaged_sidecar.py
    sidecar/.venv/Scripts/python.exe -u tools/verify_packaged_sidecar.py --language java

Why this exists
---------------
README §六 的完成定义第 2 条是「判题在打包后仍可用」。这一条**只验一种语言
是不够的**：2026-09-20 就漏掉了整条 Python 链路（冻结后 `sys.executable` 变成
sidecar 自己的 exe，见 README §六 与提交 `b70ea20`）。当时的验法是临时手敲的，
所以每换一种语言就要重来一遍。这个脚本把它固定下来：起 exe → 探 env-check →
对每种语言提交 AC / CE / TLE 三条程序 → 比对判定。

它**只认打包产物**，不测源码运行态——源码态的同一批用例在
`tests/test_multilang_judge.py` 里，跑 pytest 即可。

判定为什么是 AC / CE / TLE 这三条
---------------------------------
这三条分别走过编译成功、编译失败、运行超时三条不同路径。CE 尤其重要：打包时
漏了编译器或临时目录，表现正是「`/health` 正常但一判题就 CE」。

用法与前提
----------
- 先有打包产物：`bash sidecar/packaging/build_sidecar.sh`。
- 工具链路径从 `sidecar/.env` 读（`CPP_COMPILER_PATH` / `JAVA_COMPILER_PATH` /
  `JAVA_RUNTIME_PATH` / `PYTHON_INTERPRETER_PATH`），所以本机直接可跑；
  打包版自己不含 `.env`，脚本是把这些值当环境变量传给 exe 的。
- 判题一律 `record=false`：只算不落库，因此**不需要 PostgreSQL**。
- `--language` 缺省 `all`；某种语言的工具链没配就跳过并说明，不算失败。

⚠️ 已知：Java 的 CE 输出是乱码（`javac` 在中文 Windows 上按 GBK 输出本地化诊断，
而沙箱统一按 UTF-8 解码）。判定本身正确，只是信息不可读——见 README §九 与队列 T17。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXE = REPO / "dist" / "algoforge-sidecar" / "algoforge-sidecar.exe"
ENV_FILE = REPO / "sidecar" / ".env"

#: Keys copied from sidecar/.env into the exe's environment. Only toolchain
#: locations: everything else the script sets itself.
_PASSTHROUGH = (
    "CPP_COMPILER_PATH",
    "CPP_COMPILER_BIN_DIR",
    "PYTHON_INTERPRETER_PATH",
    "JAVA_COMPILER_PATH",
    "JAVA_RUNTIME_PATH",
    "JAVA_MAX_HEAP_MB",
    "JAVA_RSS_ALLOWANCE_MB",
)

_SUM_CASES = [
    {"input": "3 5\n", "expected_output": "8\n"},
    {"input": "-5 5\n", "expected_output": "0\n"},
]

_PROGRAMS = {
    "cpp": {
        "ac": "#include <iostream>\nint main() { int a, b; std::cin >> a >> b;"
        " std::cout << a + b << std::endl; }\n",
        "ce": "int main() { this is not c++ }\n",
        "tle": "int main() { volatile long s = 0; while (true) { s++; } }\n",
    },
    "python": {
        "ac": "a, b = map(int, input().split())\nprint(a + b)\n",
        "ce": "def broken(:\n",
        "tle": "while True:\n    pass\n",
    },
    "java": {
        # Java 8 syntax only: the JDK on this machine is 1.8.0_291.
        "ac": (
            "import java.util.*;\n"
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            "        Scanner sc = new Scanner(System.in);\n"
            "        int a = sc.nextInt();\n"
            "        int b = sc.nextInt();\n"
            "        System.out.println(a + b);\n"
            "    }\n"
            "}\n"
        ),
        "ce": "public class Main {\n    int x = ;\n}\n",
        "tle": (
            "public class Main {\n"
            "    public static void main(String[] args) {\n"
            "        long s = 0;\n"
            "        while (true) { s++; }\n"
            "    }\n"
            "}\n"
        ),
    },
}

#: CE never reaches the test cases, so it has no expected pass count.
_EXPECTED = {"ac": "AC", "ce": "CE", "tle": "TLE"}


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines from a dotenv file, ignoring comments."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _build_env(env_file: dict[str, str], port: int, token: str) -> dict[str, str]:
    """The environment the packaged exe is started with.

    ``PYTHONPATH`` is dropped deliberately: a leftover value makes the frozen
    build import the *source* tree, so the smoke would silently stop testing
    the packaged artifact.
    """
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    for key in _PASSTHROUGH:
        if env_file.get(key):
            env[key] = env_file[key]
    env.update(
        {
            "SIDECAR_PORT": str(port),
            "SIDECAR_TOKEN": token,
            "DATABASE_URL": "sqlite:///C:/Windows/Temp/af_packaged_smoke.db",
            # The packaged exe must not be reached through a proxy.
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
            "http_proxy": "",
            "https_proxy": "",
        }
    )
    return env


class _Client:
    """Minimal JSON client that always bypasses the proxy."""

    def __init__(self, base: str, token: str) -> None:
        self._base = base
        self._token = token
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        timeout: int = 180,
    ) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self._base + path, data=data, method=method)
        request.add_header("X-Sidecar-Token", self._token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with self._opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode())


def _wait_for_health(client: _Client, process: subprocess.Popen, seconds: int) -> bool:
    for _ in range(seconds):
        if process.poll() is not None:
            print(f"!! exe 提前退出，rc={process.returncode}", file=sys.stderr)
            return False
        try:
            if client.call("GET", "/health", timeout=3).get("status") == "ok":
                return True
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(1)
    return False


def _judge_one(client: _Client, language: str, kind: str) -> tuple[str, dict]:
    """Submit one program and return ``(expected, response)``."""
    payload = {
        "code": _PROGRAMS[language][kind],
        "language": language,
        "test_cases": _SUM_CASES,
        "time_limit": 15.0 if kind != "tle" else 5.0,
        "mem_limit_mb": 256,
        "record": False,
    }
    return _EXPECTED[kind], client.call("POST", "/api/v1/judge", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--language",
        default="all",
        choices=["all", *_PROGRAMS],
        help="which language to verify (default: all)",
    )
    parser.add_argument("--port", type=int, default=8125)
    parser.add_argument("--token", default="packaged-smoke-token")
    parser.add_argument(
        "--log",
        type=Path,
        default=REPO / ".workbuddy-ai" / "logs" / "packaged_sidecar_smoke.log",
    )
    args = parser.parse_args()

    if not EXE.is_file():
        print(f"!! 找不到打包产物：{EXE}")
        print("   先跑 bash sidecar/packaging/build_sidecar.sh")
        return 2

    languages = list(_PROGRAMS) if args.language == "all" else [args.language]
    env_file = _read_env_file(ENV_FILE)
    env = _build_env(env_file, args.port, args.token)
    base = f"http://127.0.0.1:{args.port}"

    args.log.parent.mkdir(parents=True, exist_ok=True)
    handle = args.log.open("w", encoding="utf-8")
    print(f"[exe] {EXE}\n[log] {args.log}")
    process = subprocess.Popen(
        [str(EXE)], env=env, stdout=handle, stderr=subprocess.STDOUT
    )
    client = _Client(base, args.token)
    try:
        if not _wait_for_health(client, process, 40):
            print("!! /health 一直没起来，见日志", file=sys.stderr)
            return 1
        print("[exe] /health ok")

        report = client.call("GET", "/api/v1/env-check", timeout=90)
        available = {item["id"]: item for item in report.get("languages", [])}
        print("\n=== env-check ===")
        for language, item in available.items():
            mark = "OK " if item.get("available") else "缺失"
            detail = item.get("version") or item.get("detail")
            print(f"  [{mark}] {language:7s} {detail}")

        failures: list[str] = []
        skipped: list[str] = []
        print("\n=== 判题 ===")
        for language in languages:
            if not available.get(language, {}).get("available"):
                skipped.append(language)
                print(f"  {language:7s} 跳过：env-check 报工具链不可用")
                continue
            for kind in ("ac", "ce", "tle"):
                expected, body = _judge_one(client, language, kind)
                actual = body.get("status")
                ok = actual == expected
                if not ok:
                    failures.append(
                        f"{language}-{kind}: 期望 {expected}，实得 {actual}"
                    )
                passed = f"{body.get('passed')}/{body.get('total')}"
                print(
                    f"  [{'OK ' if ok else 'FAIL'}] {language:7s} {kind.upper():3s}"
                    f" 期望 {expected:3s} 实得 {str(actual):3s} 用例 {passed}"
                )
                if kind == "ce" and (body.get("compile_output") or "").strip():
                    first = body["compile_output"].strip().splitlines()[0]
                    print(f"          编译输出首行：{first[:90]}")

        print("\n=== 汇总 ===")
        if skipped:
            print(f"  跳过（工具链未配置）：{', '.join(skipped)}")
        if failures:
            for line in failures:
                print(f"  ✖ {line}")
            return 1
        print("  ✔ 所有已配置语言的三条判定全部符合预期")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        handle.close()


if __name__ == "__main__":
    sys.exit(main())
