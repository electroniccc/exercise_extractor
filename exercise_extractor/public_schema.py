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


def _public_bbox(value: Any) -> list[dict[str, Any]]:
    """把调用方/内部 bbox 收敛为固定、可 JSON 序列化的公共格式。"""
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        page = item.get("page")
        bbox = item.get("bbox")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            continue
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        try:
            coordinates = [float(coordinate) for coordinate in bbox]
        except (TypeError, ValueError):
            continue
        if coordinates[2] <= coordinates[0] or coordinates[3] <= coordinates[1]:
            continue
        result.append({"page": page, "bbox": coordinates})
    return result


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
        "bbox": _public_bbox(item.get("bbox")),
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
    if not node.get("has_exercise"):
        return None
    exercises = [
        _public_exercise(item)
        for item in node.get("exercises", [])
        if _is_numbered_exercise(item)
    ]
    if not exercises:
        return None

    # 延迟导入，避免 postprocess -> extraction -> public_schema 的循环导入。
    from .postprocess import render_exercise_markdown

    # 公共 content 是“题号 + 题目 + 解/答”的纯序列；section/title/path
    # 已单独提供，不再把渲染时生成的 ``## ...`` 小节标题重复放进去。
    # JSON 中保留规范化后的 number 供匹配使用，但展示 Markdown 使用
    # 解析器记录的 original_number（例如 5.10），避免题号被重编号覆盖。
    render_exercises = deepcopy(exercises)
    for item in render_exercises:
        if item.get("original_number"):
            item["number"] = item["original_number"]
    original = render_exercise_markdown(
        {**node, "exercises": render_exercises}, include_section_headings=False
    )
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
    from .postprocess import render_answer_markdown

    answer_node = {**node, "is_answers": True, "answer_sections": answer_sections}
    return {
        "document_name": document_name or node.get("document_name") or "",
        "title": node.get("title") or "",
        "path": path or node.get("path") or node.get("_section_path") or node.get("title") or "",
        "is_answers": True,
        "exercise_page_range": list(node.get("exercise_page_range") or []),
        "answer_sections": deepcopy(answer_sections),
        "content": {
            "original": render_answer_markdown(answer_node),
            "basic_review": None,
            "tool_review": None,
        },
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
