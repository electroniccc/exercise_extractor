"""公共 JSON 数据结构与内部目录树之间的投影。

内部提取管线继续使用带 ``children`` 的 SectionNode；公共接口只暴露扁平、
可 JSON 序列化的 section。这个模块是两种表示之间的唯一适配层，避免调用方
依赖 OCR 和答案匹配过程中的临时字段。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _is_numbered_exercise(item: dict[str, Any]) -> bool:
    """只保留真正的习题编号，排除“例1/例题”等教材示例。"""
    number = item.get("number")
    if number in (None, "__preamble__"):
        return False
    normalized = str(number).strip().lower()
    return not normalized.startswith(("例", "例题", "example"))


def _public_exercise(item: dict[str, Any]) -> dict[str, Any]:
    """把内部 Exercise 归一化为固定字段的 JSON object。"""
    return {
        "number": str(item.get("number") or ""),
        "original_number": item.get("original_number"),
        "separator": item.get("separator") or ".",
        "content": item.get("content") or "",
        "solution_steps": item.get("solution_steps") or "",
        "answer": item.get("answer") or "",
        "question_type": item.get("question_type") or "short_answer",
        "section": item.get("section"),
        "section_key": item.get("section_key"),
        "pages": list(item.get("pages") or []),
    }


def _review_content(node: dict[str, Any], original: str) -> dict[str, Any]:
    source = node.get("content")
    if not isinstance(source, dict):
        source = {}
    return {
        # exercises 可能刚经过 answer matching，必须用当前题目/答案重新
        # 渲染 original；否则会出现 exercises.answer 已更新但 Markdown 仍
        # 是匹配前旧内容的情况。
        "original": original,
        "basic_review": source.get("basic_review"),
        "tool_review": source.get("tool_review"),
    }


def public_section_from_node(
    node: dict[str, Any],
    *,
    path: str | None = None,
    document_name: str | None = None,
) -> dict[str, Any] | None:
    """把一个内部习题节点投影成公共 section；无编号习题时返回 ``None``。"""
    exercises = [
        _public_exercise(item)
        for item in node.get("exercises", [])
        if _is_numbered_exercise(item)
    ]
    if not exercises:
        return None

    # 延迟导入，避免 postprocess -> extraction -> public_schema 的循环导入。
    from .postprocess import render_exercise_markdown

    original = render_exercise_markdown({**node, "exercises": exercises})
    answer_match = node.get("answer_match")
    if not isinstance(answer_match, dict):
        answer_match = {
            "matched": False,
            "count_match": False,
            "source_path": None,
            "matched_count": None,
        }
    review_warnings = node.get("review_warnings")
    if not isinstance(review_warnings, list):
        review_warnings = []
    return {
        "document_name": document_name or node.get("document_name") or "",
        "title": node.get("title") or "",
        "path": path or node.get("path") or node.get("_section_path") or node.get("title") or "",
        "has_exercise": True,
        "exercise_page_range": list(node.get("exercise_page_range") or []),
        "implicit": bool(node.get("implicit", False)),
        "tail_search_used": bool(node.get("tail_search_used", False)),
        "exercises": exercises,
        "content": _review_content(node, original),
        "answer_match": deepcopy(answer_match),
        "review_warnings": list(review_warnings),
    }


def public_answer_section_from_node(
    node: dict[str, Any],
    *,
    path: str | None = None,
    document_name: str | None = None,
) -> dict[str, Any]:
    """把内部答案节点投影成供公共 ``match_answers`` 使用的 JSON。"""
    answer_sections = node.get("answer_sections")
    if not isinstance(answer_sections, list):
        answer_sections = []
    return {
        "document_name": document_name or node.get("document_name") or "",
        "title": node.get("title") or "",
        "path": path or node.get("path") or node.get("_section_path") or node.get("title") or "",
        "is_answers": True,
        "exercise_page_range": list(node.get("exercise_page_range") or []),
        "answer_sections": deepcopy(answer_sections),
    }


def flatten_public_sections(
    nodes: list[dict[str, Any]], *, document_name: str = ""
) -> list[dict[str, Any]]:
    """按目录树顺序展平所有有习题的节点，每个结果不带 ``children``。"""
    result: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]], parent_path: str = "") -> None:
        for node in items:
            title = str(node.get("title") or "")
            path = f"{parent_path}/{title}" if parent_path else title
            public = public_section_from_node(
                node, path=path, document_name=document_name
            )
            if public is not None:
                result.append(public)
            walk(node.get("children", []), path)

    walk(nodes)
    return result


def normalize_public_section(section: dict[str, Any]) -> dict[str, Any]:
    """规范化调用方传入的 section，返回独立的 JSON-compatible object。"""
    if not isinstance(section, dict):
        raise TypeError("section 必须是 JSON object（Python dict）")
    normalized = public_section_from_node(
        deepcopy(section),
        path=section.get("path"),
        document_name=section.get("document_name"),
    )
    if normalized is None:
        raise ValueError("section.exercises 必须至少包含一道编号习题")
    return normalized
