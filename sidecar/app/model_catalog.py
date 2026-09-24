"""AI 模型目录与运行时切换（PU-13）。

为什么不加数据库表
------------------
模型选择是**这台机器上的一次会话偏好**，不是教学数据：它不进成绩、
不参与班级统计、不需要跨设备同步，也没有审计要求。为它建一张表意味着
一次迁移、一个 CRUD 接口和一套"学生 A 的选择会不会影响学生 B"的
权限讨论 —— 而正确答案是"根本不共享"。

所以这里用进程内的一个变量保存当前选择。重启后端回到 ``.env`` 里的
配置，这正是"简单配置"该有的行为：想固定就用 ``.env``，想临时换用
界面。

为什么不是任意模型名
--------------------
可选项来自 ``settings`` 里**已经配好的** provider，而不是一个自由
输入框。自由输入意味着学生可以填一个不存在的模型名，失败发生在
第一次提问时、报错是 provider 的 404 —— 那是一个本该在选单里就
避免掉的挫败。选单里出现的每一项都是这里验证过能构建出 ProviderSpec
的。

为什么只允许三个具名 provider
------------------------------
与 ``gateway._PROVIDER_NAMES`` 保持同一集合。多出来的名字会在
``_resolve_provider`` 里抛 LLMConfigError，与其让那个异常在提问时
才冒出来，不如在这里就过滤掉。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.llm.gateway import _PROVIDER_NAMES, LLMConfigError, _resolve_provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelOption:
    """A selectable model. One provider may contribute one option."""

    #: Identifier used in requests. Same string as the provider name.
    id: str
    #: What the picker shows.
    label: str
    #: The model name that will actually be sent.
    model: str
    #: The endpoint, shown as a tooltip so a student can tell local from hosted.
    base_url: str
    #: True when no credential is needed (Ollama), which is worth surfacing.
    local: bool


#: Display names. Kept beside the options rather than in the settings, because
#: they are UI copy and would otherwise be a fourth place to change when a
#: provider is added.
_LABELS: dict[str, str] = {
    "deepseek": "DeepSeek",
    "ollama": "本机 Ollama",
    "openai_compatible": "自定义端点",
}

# The two constants must describe the same set: a name in _PROVIDER_NAMES the
# gateway can route to, but missing here, would be silently absent from the
# picker; one here that the gateway cannot route would 500 on selection.
# Asserted at import so it fails loudly in the test suite rather than as a
# missing menu entry nobody thinks to check.
assert set(_LABELS) == set(_PROVIDER_NAMES), (
    f"model_catalog._LABELS {sorted(_LABELS)} and "
    f"gateway._PROVIDER_NAMES {sorted(_PROVIDER_NAMES)} disagree"
)

#: The provider the app boots with, and what "reset" goes back to. Captured at
#: import time: ``settings.llm_provider`` never changes at runtime, so reading
#: it once here documents that the value is the file's, not a live lookup.
_DEFAULT_PROVIDER: str = (settings.llm_provider or "").strip().lower()

#: The runtime override. ``None`` means "use the .env value".
_current_provider: Optional[str] = None


def _option_for(name: str) -> Optional[ModelOption]:
    """Build the option for ``name``, or ``None`` when it is not usable.

    "Usable" means the same thing it means to the gateway: a ProviderSpec can
    be built without an error. A provider with an empty API key raises
    LLMConfigError there, and the right response is to omit it from the list
    rather than to offer a choice that fails on first use.
    """
    try:
        spec = _resolve_provider(name)
    except LLMConfigError as exc:
        # Expected for e.g. deepseek with no key. Not an error condition:
        # that provider is simply not available on this machine.
        logger.debug("provider %s unavailable: %s", name, exc)
        return None
    except Exception:  # pragma: no cover - defensive
        logger.warning("provider %s could not be resolved", name, exc_info=True)
        return None

    return ModelOption(
        id=spec.name,
        label=_LABELS.get(spec.name, spec.name),
        model=spec.model,
        base_url=spec.base_url,
        local=not spec.send_auth,
    )


def available_models() -> list[ModelOption]:
    """Every provider configured enough to actually answer a request.

    Ordered with the configured default first: the picker should open on the
    one that will be used if the student does nothing.
    """
    options: list[ModelOption] = []
    seen: set[str] = set()

    for name in _LABELS:
        opt = _option_for(name)
        if opt is not None:
            options.append(opt)
            seen.add(opt.id)

    default = active_provider()
    options.sort(key=lambda o: (o.id != default, o.label))
    return options


def active_provider() -> str:
    """The provider a request will use right now.

    An override pointing at something no longer configured is ignored rather
    than honoured: it would otherwise turn into a failure on the next
    question, caused by a change nobody made in this session.
    """
    if _current_provider is not None:
        if _option_for(_current_provider) is not None:
            return _current_provider
        logger.warning(
            "model override %r is no longer available; falling back to %r",
            _current_provider,
            _DEFAULT_PROVIDER,
        )
    return _DEFAULT_PROVIDER


def set_provider(name: Optional[str]) -> str:
    """Switch the active provider; ``None`` restores the ``.env`` value.

    Raises:
        ValueError: ``name`` is not one of the configured providers. Raised
            as a plain ``ValueError`` because the API layer turns it into a
            400 — it is a bad request, not a server failure.
    """
    global _current_provider

    if name is None or name == "":
        _current_provider = None
        return active_provider()

    key = str(name).strip().lower()
    if key not in _LABELS:
        raise ValueError(
            f"unknown model {name!r}; expected one of {', '.join(sorted(_LABELS))}"
        )
    if _option_for(key) is None:
        raise ValueError(
            f"model {name!r} is not configured on this machine "
            "(missing credential or endpoint)"
        )

    _current_provider = key
    return key


def reset() -> None:
    """Forget the runtime override. Test hook and process-restart behaviour."""
    global _current_provider
    _current_provider = None


def snapshot() -> dict:
    """The payload the settings UI renders."""
    return {
        "active": active_provider(),
        "default": _DEFAULT_PROVIDER,
        "models": [
            {
                "id": o.id,
                "label": o.label,
                "model": o.model,
                "base_url": o.base_url,
                "local": o.local,
            }
            for o in available_models()
        ],
    }
