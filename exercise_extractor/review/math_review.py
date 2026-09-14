'''数学内容审校（暂保留，默认不调用）。

该模块用于后续基于 SymPy 的数学核验；当前 CLI 默认关闭。
'''

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .basic_review import (
    DEFAULT_REVIEW_MODEL,
    _chat_json,
    _EXERCISE_START_RE,
    _HEADING_RE,
    _exercise_signatures,
    _log_structure_mismatch,
    _make_client,
    _message_content,
    _parse_json_response,
    _remove_unexpected_answer_segments,
    _structure_is_preserved,
)
from ..llm_usage import UsageCallback, report_usage, usage_from_response

logger = logging.getLogger(__name__)

REVIEW_STATUSES = (
    "无需修复",
    "已修复",
    "高度怀疑有问题",
    "无法验证",
)
_STATUS_RE = re.compile(r"\s*<!--\s*审校状态：[^>]*-->\s*")


def _normalize_status(value: Any) -> str:
    aliases = {
        "无需修改": "无需修复",
        "无问题": "无需修复",
        "已修改": "已修复",
        "疑似有问题": "高度怀疑有问题",
        "待人工确认": "高度怀疑有问题",
        "无法判断": "无法验证",
    }
    value = aliases.get(str(value or "").strip(), str(value or "").strip())
    return value if value in REVIEW_STATUSES else "无法验证"


def _statuses_from_payload(payload: dict[str, Any], count: int) -> list[str]:
    raw = payload.get("statuses")
    statuses: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                item = item.get("status")
            statuses.append(_normalize_status(item))
    if len(statuses) != count:
        return ["无法验证"] * count
    return statuses


def _status_summary(statuses: list[str]) -> str:
    counts = {status: statuses.count(status) for status in REVIEW_STATUSES}
    return ", ".join(f"{status}={counts[status]}" for status in REVIEW_STATUSES)


def _append_statuses(markdown: str, statuses: list[str]) -> str:
    """把数学审校状态注释放在每道题末尾。"""
    markdown = _STATUS_RE.sub("\n", markdown).rstrip()
    starts = list(_EXERCISE_START_RE.finditer(markdown))
    if not starts or len(starts) != len(statuses):
        return markdown
    inserts: list[tuple[int, str]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        heading = _HEADING_RE.search(markdown, match.end(), end)
        position = heading.start() if heading else end
        inserts.append((position, f"\n\n<!-- 审校状态：{statuses[index]} -->\n\n"))
    for position, insertion in reversed(inserts):
        markdown = markdown[:position].rstrip() + insertion + markdown[position:].lstrip()
    return markdown.rstrip() + "\n"


def remove_review_statuses(markdown: str) -> str:
    """移除数学审校状态注释。"""
    return _STATUS_RE.sub("\n", markdown)


def _fallback(markdown: str) -> str:
    return _append_statuses(markdown, ["无法验证"] * len(_exercise_signatures(markdown)))

_SYMPY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sympy_equivalent",
            "description": "判断两个 SymPy 表达式是否恒等价。输入使用 SymPy 语法，不是 LaTeX。",
            "parameters": {
                "type": "object",
                "properties": {"expression_a": {"type": "string"}, "expression_b": {"type": "string"}},
                "required": ["expression_a", "expression_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sympy_simplify",
            "description": "化简一个 SymPy 表达式。",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sympy_solve",
            "description": "求解 SymPy 方程或表达式。",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}, "symbol": {"type": "string"}}, "required": ["expression", "symbol"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sympy_diff",
            "description": "对 SymPy 表达式求导。",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}, "symbol": {"type": "string"}}, "required": ["expression", "symbol"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sympy_integrate",
            "description": "对 SymPy 表达式积分。",
            "parameters": {"type": "object", "properties": {"expression": {"type": "string"}, "symbol": {"type": "string"}}, "required": ["expression", "symbol"]},
        },
    },
]


def _sympy_expression(text: str) -> Any:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    if not isinstance(text, str) or len(text) > 3000:
        raise ValueError("表达式为空或过长")
    if "__" in text or not re.fullmatch(r"[A-Za-z0-9_+*/^().,=<>\[\]{}|!%\-\s]+", text):
        raise ValueError("表达式包含不允许的字符")
    names = set(re.findall(r"[A-Za-z_]\w*", text))
    functions = {"sin", "cos", "tan", "asin", "acos", "atan", "exp", "log", "sqrt", "Abs"}
    locals_map: dict[str, Any] = {name: getattr(sp, name) for name in functions}
    locals_map.update({"pi": sp.pi, "E": sp.E, "oo": sp.oo})
    for name in names - set(locals_map):
        locals_map[name] = sp.Symbol(name)
    transformations = standard_transformations + (convert_xor, implicit_multiplication_application)
    if "=" in text and "==" not in text:
        left, right = text.split("=", 1)
        return sp.Eq(parse_expr(left, local_dict=locals_map, transformations=transformations), parse_expr(right, local_dict=locals_map, transformations=transformations))
    return parse_expr(text, local_dict=locals_map, transformations=transformations)


def _run_sympy_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    import sympy as sp

    try:
        if name == "sympy_equivalent":
            a, b = _sympy_expression(args["expression_a"]), _sympy_expression(args["expression_b"])
            if isinstance(a, sp.Equality):
                a = a.lhs - a.rhs
            if isinstance(b, sp.Equality):
                b = b.lhs - b.rhs
            result = sp.simplify(a - b)
            return {"ok": True, "equivalent": bool(result == 0 or result.equals(0)), "simplified_difference": str(result)}
        if name == "sympy_simplify":
            return {"ok": True, "result": str(sp.simplify(_sympy_expression(args["expression"]))) }
        if name in {"sympy_solve", "sympy_diff", "sympy_integrate"}:
            expression = _sympy_expression(args["expression"])
            symbol = sp.Symbol(str(args["symbol"]))
            if name == "sympy_solve":
                result = sp.solve(expression, symbol)
            elif name == "sympy_diff":
                result = sp.diff(expression, symbol)
            else:
                result = sp.integrate(expression, symbol)
            return {"ok": True, "result": str(result)}
        raise ValueError(f"未知工具：{name}")
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def _tool_calls(message: Any) -> list[Any]:
    return list(getattr(message, "tool_calls", None) or [])


def review_markdown_with_tools(
    content: str,
    document_name: str,
    section_name: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    client: Any = None,
    max_rounds: int = 5,
    strict: bool = False,
    usage_callback: UsageCallback | None = None,
) -> str:
    """整节执行数学核验，最多 max_rounds 次模型对话，并按需调用 SymPy。"""
    if max_rounds < 1 or max_rounds > 5:
        raise ValueError("max_rounds 必须在 1 到 5 之间")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content 必须是非空 Markdown 字符串")
    exercise_count = len(_exercise_signatures(content))
    logger.info(
        "数学审校开始：文档=%s，小节=%s，模型=%s，题数=%d，最大轮数=%d，字符数=%d",
        document_name,
        section_name,
        model,
        exercise_count,
        max_rounds,
        len(content),
    )
    llm = _make_client(client, base_url, api_key)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                f"你是数学教材习题审校器。输入是一整个小节，必须按题号逐题检查题目、"
                "解和答。可以调用 SymPy 工具核对代数等价、方程、导数和积分；工具输入"
                "必须转换成 SymPy 语法。只有有充分依据时才修复，不能凭空补全 OCR 缺失内容。"
                f"本次最多进行 {max_rounds} 轮对话，请规划好检查顺序并尽量在这 {max_rounds} 轮内完成校对；"
                "每轮可在同一次响应中发起多个 SymPy 工具调用，遇到多个相互独立的运算时应尽量批量验证，"
                "不要一个运算占用一整轮。即使达到最后一轮只能完成部分校对，也必须返回已经完成的部分，"
                "不得因为还有题目未核验就丢弃前面已经完成的修复；最后一轮必须直接返回 JSON，"
                "不要在最后一轮只发工具调用而不返回结果。在需要调用工具的轮次，也请在响应 content 中"
                "同时提供截至当前的完整 Markdown 草稿和 statuses（可与多个 tool_calls 同时返回），"
                "以便即使达到上限也能保留部分进展。"
                "严格保留每道题原有的解答结构：逐题保留原有的解：/答：标记数量、"
                "顺序和归属；原题没有解或答时，必须仍然没有解或答。绝对禁止新增、"
                "删除、移动、拆分或合并解：/答：段落，禁止根据题目、答案章节、上下文"
                "或数学常识推测、补写、改写任何缺失的解答。只能核对输入中已经存在的"
                "题目、解和答；缺少内容时应保留缺失状态并标记高度怀疑有问题或无法验证。"
                "如果无法确认，保留原文并标记高度怀疑有问题或无法验证。必须返回 JSON："
                "{markdown:string,statuses:[{status:string}]}，statuses 与题目数量和顺序一致。"
                "不要在 markdown 中添加审校注释，不要改变题号、题目数量或解/答结构。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"文档名称：{document_name}\n当前小节：{section_name}\n"
                "请检查以下整节内容：\n<markdown>\n" + content + "\n</markdown>"
            ),
        },
    ]
    best_revised = content
    best_statuses = ["无法验证"] * exercise_count
    best_round = 0
    try:
        for round_index in range(1, max_rounds + 1):
            logger.debug(
                "数学审校调用模型：文档=%s，小节=%s，第%d/%d轮，消息数=%d",
                document_name,
                section_name,
                round_index,
                max_rounds,
                len(messages),
            )
            response = _chat_json(llm, model, messages, tools=_SYMPY_TOOLS)
            report_usage(
                usage_callback,
                usage_from_response(
                    response,
                    operation="review.math_with_tools",
                    model=model,
                ),
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("LLM 没有返回 choices")
            message = getattr(choices[0], "message", None)
            calls = _tool_calls(message)
            payload = _parse_json_response(_message_content(response))
            candidate = payload.get("markdown") if payload else None
            if isinstance(candidate, str):
                candidate, removed_answers = _remove_unexpected_answer_segments(
                    content, candidate
                )
                if removed_answers:
                    logger.info(
                        "数学审校删除模型补出的解答：文档=%s，小节=%s，第%d轮，题数=%d",
                        document_name,
                        section_name,
                        round_index,
                        removed_answers,
                    )
                if _structure_is_preserved(content, candidate):
                    best_revised = candidate
                    best_statuses = _statuses_from_payload(payload, exercise_count)
                    best_round = round_index
                elif not calls:
                    _log_structure_mismatch("数学审校", content, candidate)
            if not calls:
                if not isinstance(candidate, str) or not payload:
                    raise ValueError("LLM 返回中缺少有效 Markdown")
                if not _structure_is_preserved(content, candidate):
                    raise ValueError("LLM 返回的 Markdown 结构发生变化")
                statuses = _statuses_from_payload(payload, exercise_count)
                result = _append_statuses(
                    candidate, statuses
                )
                logger.info(
                    "数学审校完成：文档=%s，小节=%s，第%d轮结束，工具轮数=%d，状态=[%s]，内容%s变化",
                    document_name,
                    section_name,
                    round_index,
                    round_index - 1,
                    _status_summary(statuses),
                    "有" if candidate != content else "无",
                )
                return result
            logger.info(
                "数学审校请求工具：文档=%s，小节=%s，第%d轮，工具=%s",
                document_name,
                section_name,
                round_index,
                ",".join(
                    getattr(getattr(call, "function", None), "name", "未知")
                    for call in calls
                ),
            )
            assistant_call = {
                "role": "assistant",
                "content": getattr(message, "content", None),
                "tool_calls": [
                    {
                        "id": getattr(call, "id", ""),
                        "type": "function",
                        "function": {
                            "name": getattr(getattr(call, "function", None), "name", ""),
                            "arguments": getattr(getattr(call, "function", None), "arguments", "{}"),
                        },
                    }
                    for call in calls
                ],
            }
            messages.append(assistant_call)
            for call in calls:
                function = getattr(call, "function", None)
                name = getattr(function, "name", "")
                try:
                    arguments = json.loads(getattr(function, "arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                result = _run_sympy_tool(name, arguments)
                logger.log(
                    logging.DEBUG if result.get("ok") else logging.WARNING,
                    "SymPy 工具完成：文档=%s，小节=%s，工具=%s，ok=%s",
                    document_name,
                    section_name,
                    name,
                    result.get("ok"),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": getattr(call, "id", ""),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            if round_index == max_rounds:
                if best_round:
                    logger.warning(
                        "数学审校达到最大轮数，保留部分结果：文档=%s，小节=%s，来自第%d轮",
                        document_name,
                        section_name,
                        best_round,
                    )
                    return _append_statuses(best_revised, best_statuses)
                raise ValueError("达到最大模型对话轮数，未得到可用结果")
    except Exception as exc:
        logger.warning(
            "数学审校失败，保留原文：文档=%s，小节=%s，错误=%s: %s",
            document_name,
            section_name,
            type(exc).__name__,
            exc,
        )
        logger.debug("数学审校异常详情", exc_info=True)
        if strict:
            raise
        if best_round:
            logger.warning(
                "数学审校异常，保留已完成的部分结果：文档=%s，小节=%s，来自第%d轮",
                document_name,
                section_name,
                best_round,
            )
            return _append_statuses(best_revised, best_statuses)
        return _fallback(content)
