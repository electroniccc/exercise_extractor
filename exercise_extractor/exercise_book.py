"""习题集（exercise_book）专用提取：与 textbook 逻辑解耦。

- 有目录：按目录层级生成整节页码范围，叶子小节整体作为习题区域；
- 无目录：整本作为一个区域，OCR 后按小标题层级切块，再按题号提取；
- 按题号切分（split_exercises）、图注回插、HTML 表格转换与 textbook 共用。

习题集默认每节都是习题；节内可能出现"学习要点/知识点"等用 1 2 3 列出的
前置内容，目前按普通内容一并提取，具体过滤规则后续按教材情况补充。
"""

import logging
import re

from .extraction import (
    SectionNode,
    attach_figures,
    convert_exercise_tables,
    demote_exercise_headings,
    extract_figures,
    truncate_before_heading,
    truncate_to_section_heading,
)
from .parse_md_exercises import (
    HEADING_RE,
    Exercise,
    page_marker,
    split_exercises,
    split_solutions_for_section,
    truncate_before_page_heading,
    truncate_from_page_heading,
)
from .toc_detect import is_skippable_title

logger = logging.getLogger(__name__)

# 习题集常见题号格式："习题 1."、"习题1．"、"练习 3." → 归一为 "1."、"3."
# 只处理数字后跟单段分隔符的（不碰 "习题 1-2" 这类范围/小节引用）
_EXERCISE_PREFIX_RE = re.compile(
    r"^(\s*)(?:习题|练习|题)\s*(\d{1,3}\s*[.．、)）])", re.MULTILINE
)


def _normalize_exercise_prefixes(content: str) -> str:
    """把“习题 N.”类题号前缀去掉，让通用题号切分能识别。"""
    return _EXERCISE_PREFIX_RE.sub(r"\1\2", content)


def build_exercise_book_sections(
    toc_tree, doc_page_count: int | None
) -> list[SectionNode]:
    """生成习题集的习题区域。

    有目录：叶子小节整体作为习题区域（范围 [起始页, 下一节点首页)，含边界页，
    提取时按边界标题截断）；无目录：返回整本占位节点（no_toc=True），
    由提取阶段按小标题层级切块。
    """
    if not toc_tree:
        logger.info("习题集未检测到目录，整本 OCR 后按小标题层级分割")
        return [
            {
                "title": "全书习题",
                "has_exercise": True,
                "no_toc": True,
                "children": [],
                "exercise_page_range": [1, (doc_page_count or 3) + 1],
                "tail_search_used": False,
            }
        ]

    sections: list[SectionNode] = []

    def walk(node, node_next, ancestor_next) -> SectionNode | None:
        title = node.get("title", "")
        # 跳过前后置内容；但保留"参考答案/提示"类节点（试卷答案也是内容）
        if is_skippable_title(title) and "答案" not in title and "提示" not in title:
            return None
        boundary = node_next or ancestor_next
        result: SectionNode = {
            "title": title,
            "has_exercise": False,
            "children": [],
            "tail_search_used": False,
        }
        children = node.get("children") or []
        if children:
            for idx, child in enumerate(children):
                next_child = children[idx + 1] if idx + 1 < len(children) else None
                child_result = walk(child, next_child, boundary)
                if child_result:
                    result["children"].append(child_result)
            return result if result["children"] else None
        start_page = node.get("page_num")
        if not isinstance(start_page, int) or start_page < 1:
            return None
        end_page = start_page + 3
        if (
            boundary
            and isinstance(boundary.get("page_num"), int)
            and boundary["page_num"] > start_page
        ):
            end_page = boundary["page_num"] + 1  # 含边界页，提取时按边界标题截断
            result["boundary_page"] = boundary["page_num"]
        elif doc_page_count:
            end_page = doc_page_count + 1
        result["has_exercise"] = True
        result["exercise_page_range"] = [start_page, max(start_page + 1, end_page)]
        if boundary and boundary.get("title"):
            result["boundary_title"] = boundary["title"]
        return result

    for idx, node in enumerate(toc_tree):
        node_next = toc_tree[idx + 1] if idx + 1 < len(toc_tree) else None
        section_result = walk(node, node_next, None)
        if section_result:
            sections.append(section_result)
    return sections


def _renumber(exercises: list[Exercise]) -> None:
    """按 1,2,3… 重编号，原题号保留在 original_number。"""
    seq = 0
    for item in exercises:
        if item.get("number") == "__preamble__":
            continue
        seq += 1
        if item["number"] != str(seq):
            item["original_number"] = item["number"]
            item["number"] = str(seq)
            item["separator"] = "."


def _extract_no_toc(content: str) -> list[Exercise]:
    """无目录：整本内容按小标题层级切块，每块按题号提取（编号每块独立）。"""
    exercises: list[Exercise] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        if current_heading is None:
            current_lines = []
            return
        chunk = "\n".join(current_lines).strip()
        current_lines = []
        if not chunk:
            return
        items = split_exercises(
            chunk,
            keep_leading=False,
            check_section_end=False,
            split_solutions=False,
        )
        items = [e for e in items if e.get("number") != "__preamble__"]
        if not items:
            return
        split_solutions_for_section(
            items, current_heading, doc_type="exercise_book"
        )
        for item in items:
            item["section"] = current_heading
            item["question_type"] = "short_answer"
        _renumber(items)
        exercises.extend(items)

    for line in content.split("\n"):
        if HEADING_RE.match(line):
            flush()
            current_heading = re.sub(r"^#+\s*", "", line).strip()
            continue
        current_lines.append(line)
    flush()
    return exercises


def extract_exercises(sections: list[SectionNode]) -> None:
    """习题集版提取：每节内容整体按题号切分，不做小节归属过滤。"""

    def walk(node: SectionNode) -> None:
        if node.get("has_exercise"):
            content = node.get("exercise_content", "")
            content = _normalize_exercise_prefixes(content)
            if node.get("no_toc"):
                node["exercises"] = _extract_no_toc(content)
            else:
                # 从本节点自己的标题行开始；按边界标题截断，避免串入下一节
                content = truncate_to_section_heading(content, node["title"])
                # 起始页标题兜底：目录标题可能比页内标题多前缀（如目录
                # "习题 1-2 数列的极限" vs 页内 "## 数列的极限"），
                # 按起始页第一个标题行截取，丢掉上一节溢出内容
                start_page = node.get("exercise_page_range", [None])[0]
                if isinstance(start_page, int):
                    content = truncate_from_page_heading(content, start_page)
                    # 按起始页标题截取时，标题前的 page marker 会被一并截掉；
                    # 恢复它，让首道题也能获得 pages 属性。
                    if not re.match(
                        rf"^\s*<!--\s*page:\s*{start_page}\s*-->",
                        content,
                    ):
                        content = f"{page_marker(start_page)}\n{content}"
                if node.get("boundary_title"):
                    content = truncate_before_heading(content, node["boundary_title"])
                # 边界标题写法可能与页内标题不一致（如目录"2 连续函数" vs
                # 页内"## 第2章 连续函数"）：按边界页第一个标题行兜底截断
                if node.get("boundary_page"):
                    content = truncate_before_page_heading(content, node["boundary_page"])
                content = demote_exercise_headings(content)
                content, figures = extract_figures(content)
                # 习题集每节开头可能是学习要点/正文，不拼进第一题
                exercises = split_exercises(
                    content,
                    keep_leading=False,
                    check_section_end=False,
                    split_solutions=False,
                )
                attach_figures(exercises, figures)
                convert_exercise_tables(exercises)
                for item in exercises:
                    item["question_type"] = "short_answer"
                split_solutions_for_section(
                    exercises, node.get("title"), doc_type="exercise_book"
                )
                _renumber(exercises)
                node["exercises"] = exercises
            logger.debug(f"[{node['title']}] 提取到 {len(node['exercises'])} 道习题")
        for child_node in node.get("children", []):
            walk(child_node)

    for section_node in sections:
        walk(section_node)
