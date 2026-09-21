"""``app.models`` 的清单完整性（T36）。

``app/models/__init__.py`` 的 docstring 写着它是「the single list so the test
suite does not have to repeat it (and cannot silently forget a table when a
new model is added)」。这句话一度是假的：`user.py` 与 `assignment.py` 没被它
导入，于是只 `import app.models` 就碰 `Base.metadata` 的入口会少两张表——
后果不是「少建两张表」而是**直接抛**，`submissions.process_review_by` 的外键
找不到 `users`。应用本身没事（`import app.main` 顺带导入了它们），所以这是个
只咬脚本、CLI、未来迁移工具和新写测试的坑。

这里钉住两件事：
  1. 磁盘上声明的每一张表，都必须在「只 import 包」后被注册；
  2. 每个外键指向的表，也都必须在——这才是 `create_all` 真正的硬要求。

⚠️ 为什么必须开子进程
--------------------
「遍历模块取声明的表名」和「看 metadata 里有什么」这两步**不能在同一进程里
按顺序做**：遍历要 `import` 每个模块，而 import 本身就把那张表注册进 metadata，
于是测试永远绿，连 `user.py` 没被导入都能过。所以声明清单在父进程收集，
metadata 快照在**只 import 了 `app.models` 的干净子进程**里取，两边比对。
这个坑比被它抓的那个 bug 更容易再犯——写测试的人第一反应都是同进程。

⚠️ 为什么用遍历而不是写死列表
----------------------------
写死一张表名清单就等于把这个缺陷再复制一份：下一个新模型照样会漏，而测试
照样绿。所以表名是从**磁盘上的模块**里读出来的。
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import app.models
from app.models.base import Base

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "sidecar"

# 只 import 包本身，再看 metadata 里有什么。不 import 任何单个模型模块——
# 那会把表注册进去，测量就污染了被测对象。
_SNAPSHOT_SCRIPT = """
import json
import app.models
from app.models.base import Base

tables = sorted(Base.metadata.tables)
# 用 target_fullname 而不是 fk.column：后者会去解析引用的表，在本缺陷存在时
# 直接抛 NoReferencedTableError，把一个「缺表」的事实报成「脚本崩了」。
fk_targets = sorted({fk.target_fullname.split(".")[0]
                     for t in Base.metadata.tables.values()
                     for fk in t.foreign_keys})
print(json.dumps({"tables": tables, "fk_targets": fk_targets}))
"""


def _declared_table_names() -> set[str]:
    """磁盘上 ``app/models/`` 下所有模型类声明的表名。"""
    names: set[str] = set()
    for info in pkgutil.iter_modules(app.models.__path__):
        module = importlib.import_module(f"app.models.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            table_name = getattr(obj, "__tablename__", None)
            if isinstance(table_name, str) and issubclass(obj, Base):
                names.add(table_name)
    return names


@pytest.fixture(scope="module")
def snapshot() -> dict[str, list[str]]:
    """一个只 import 了 ``app.models`` 的进程眼里的 metadata。"""
    # 清 PYTHONPATH 跑 Python：否则 sitecustomize.py 会让一堆无关的东西挂掉。
    # 用子进程的 env 参数而不是 shell 的 `env -u`，后者在本机是个空壳脚本。
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable, "-c", _SNAPSHOT_SCRIPT],
        cwd=SIDECAR,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"子进程失败：{proc.stderr}"
    return json.loads(proc.stdout)


def test_every_declared_table_is_registered(snapshot: dict[str, list[str]]) -> None:
    declared = _declared_table_names()
    registered = set(snapshot["tables"])

    missing = sorted(declared - registered)
    assert not missing, (
        f"这些表在 app/models/ 下声明了，但只 `import app.models` 之后 "
        f"Base.metadata 里没有：{missing}。"
        f"原因几乎总是 app/models/__init__.py 漏了 import —— 它自称是"
        f"「唯一清单」，漏一个就是撒谎。已注册：{sorted(registered)}"
    )


def test_every_foreign_key_target_is_registered(snapshot: dict[str, list[str]]) -> None:
    """``create_all`` 的硬要求：外键指向的表必须存在，否则直接抛。"""
    registered = set(snapshot["tables"])

    dangling = sorted({t for t in snapshot["fk_targets"] if t not in registered})
    assert not dangling, (
        f"这些表被外键引用却没有被注册：{dangling}。"
        f"`Base.metadata.create_all()` 会抛 NoReferencedTableError —— "
        f"注意是**抛**，不是「少建两张表」。已注册：{sorted(registered)}"
    )


def test_the_registry_is_not_empty(snapshot: dict[str, list[str]]) -> None:
    """防「遍历逻辑写错于是声明清单为空」——那样上面两条会Vacuously 通过。"""
    assert len(_declared_table_names()) >= 7, (
        f"只从 app/models/ 下读到 {len(_declared_table_names())} 张表，"
        f"比已知的表还少。要么遍历逻辑坏了（于是上面两条测试什么都测不到），"
        f"要么模型被搬走了而这条没跟着改。"
    )
    assert len(snapshot["tables"]) >= 7, (
        f"子进程只看到 {len(snapshot['tables'])} 张表：{snapshot['tables']}"
    )
