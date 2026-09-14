"""统一的 LLM token 用量事件。

调用方可把 ``usage_callback`` 传入提取/审校接口；每次实际模型请求完成后都会
收到一个 :class:`LlmUsage`。该模块不依赖具体的 OpenAI SDK，兼容不同服务在
``usage`` 中使用的 ``prompt_tokens/input_tokens``、``completion_tokens/output_tokens``
字段命名。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """一次 LLM 请求报告的 token 用量。未知用量保留为 ``None``。"""

    operation: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider: str = "openai_compatible"

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


UsageCallback = Callable[[LlmUsage], None]


def _field(value: Any, *names: str) -> int | None:
    for name in names:
        candidate = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
    return None


def usage_from_response(response: Any, *, operation: str, model: str) -> LlmUsage:
    """从 OpenAI-compatible 响应提取用量；服务未返回 usage 时不估算。"""
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    return LlmUsage(
        operation=operation,
        model=model,
        input_tokens=_field(usage, "prompt_tokens", "input_tokens"),
        output_tokens=_field(usage, "completion_tokens", "output_tokens"),
    )


def report_usage(callback: UsageCallback | None, usage: LlmUsage) -> None:
    """把用量交给调用方；callback 异常不应影响已完成的提取/审校结果。"""
    if callback is None:
        return
    callback(usage)
