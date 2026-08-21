"""提取后的后处理：统计报告、导出 markdown / JSON、控制台摘要。

collect_report：汇总各习题章节的提取情况（页码范围、缓存页数、习题数）；
write_section_markdown / write_section_json：单独即时导出一个章节；
write_outputs：导出每节 markdown、section JSON 和 report.json；
print_summary：控制台摘要。
"""

import json
import logging
import os
import re
from typing import NotRequired, TypedDict

from .extraction import SectionNode
from .public_schema import public_section_from_node

logger = logging.getLogger(__name__)


def _numbered_exercises(node: SectionNode) -> list[dict]:
    return [
        item
        for item in node.get("exercises", [])
        if item.get("number") not in (None, "__preamble__")
    ]


class ReportSection(TypedDict):
    """统计报告中单个习题章节的摘要。"""

    title: str
    path: str
    page_range: list[int]
    pages_total: int
    pages_cached: int
    exercise_count: int
    numbers: list[str]
    is_answers: NotRequired[bool]


class Report(TypedDict):
    """统计报告。"""

    total_exercises: int
    sections: list[ReportSection]


def collect_report(
    sections: list[SectionNode], markdown_path: str
) -> Report:
    """汇总各习题章节的提取情况，用于验证与排查 range/缺页问题。"""
    report: Report = {"total_exercises": 0, "sections": []}

    def walk(node: SectionNode, path: str = "") -> None:
        node_path = f"{path}/{node['title']}" if path else node["title"]
        if node.get("has_exercise") or node.get("is_answers"):
            start_page, end_page = node["exercise_page_range"]
            pages = list(range(start_page, end_page))
            cached = [
                p
                for p in pages
                if os.path.exists(os.path.join(markdown_path, f"page_{p}", "page.md"))
            ]
            if node.get("is_answers"):
                answer_sections = node.get("answer_sections", [])
                answers_list = [
                    e
                    for sec in answer_sections
                    for e in sec.get("answers", [])
                ]
                report["sections"].append(
                    {
                        "title": node["title"],
                        "path": node_path,
                        "page_range": [start_page, end_page],
                        "pages_total": len(pages),
                        "pages_cached": len(cached),
                        "exercise_count": len(answers_list),
                        "numbers": [e["number"] for e in answers_list],
                        "is_answers": True,
                    }
                )
                report["total_exercises"] += len(answers_list)
            else:
                exercises = _numbered_exercises(node)
                report["sections"].append(
                    {
                        "title": node["title"],
                        "path": node_path,
                        "page_range": [start_page, end_page],
                        "pages_total": len(pages),
                        "pages_cached": len(cached),
                        "exercise_count": len(exercises),
                        "numbers": [e["number"] for e in exercises],
                    }
                )
                report["total_exercises"] += len(exercises)
        for child_node in node.get("children", []):
            walk(child_node, node_path)

    for section_node in sections:
        walk(section_node)
    return report


def render_exercise_markdown(
    node: SectionNode, *, include_section_headings: bool = True
) -> str:
    """把单个习题节点渲染成 Markdown（不含文件标题）。

    公共 section JSON 的 content 是纯题目序列，不需要重复携带小节标题，
    因此公共 schema 调用时会关闭 ``include_section_headings``；CLI 文件输出
    默认保留原有标题行为。
    """
    if not node.get("has_exercise") or not node.get("exercises"):
        return ""
    chunks: list[str] = []
    last_section = None
    title_norm = re.sub(r"\s+", "", node["title"])
    for item in node["exercises"]:
        if item["number"] == "__preamble__":
            chunks.append(f"{item['content']}\n\n")
            continue
        section = item.get("section")
        section_norm = re.sub(r"\s+", "", section) if section else ""
        if include_section_headings and (
            section
            and section_norm != title_norm
            and section_norm != last_section
        ):
            chunks.append(f"## {section}\n\n")
            last_section = section_norm
        sep = item.get("separator", ".")
        chunks.append(f"*{item['number']}{sep}* {item['content']}\n\n")
        if item.get("solution_steps"):
            chunks.append(f"解：{item['solution_steps']}\n\n")
        if item.get("answer"):
            chunks.append(f"答：{item['answer']}\n\n")
    return "".join(chunks)


def render_answer_markdown(node: SectionNode) -> str:
    """把习题答案节点渲染成输出 Markdown（不含文件标题）。"""
    if not node.get("is_answers") or not node.get("answer_sections"):
        return ""
    chunks: list[str] = []
    for sec in node["answer_sections"]:
        chunks.append(f"## {sec['title']}\n\n")
        if sec.get("matched_title"):
            chunks.append(
                f"<!-- 匹配：{sec.get('matched_path') or sec['matched_title']}"
                f"（答案 {len(sec['answers'])} 题 vs 习题 "
                f"{sec.get('matched_count')} 题）-->\n\n"
            )
        for item in sec["answers"]:
            sep = item.get("separator", ".")
            chunks.append(f"*{item['number']}{sep}* {item['content']}\n\n")
    return "".join(chunks)


def _sanitize_filename(title: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")


def _markdown_output_title(node: SectionNode) -> str:
    title = node["title"]
    if node.get("implicit"):
        return f"{title}（节尾习题）"
    return title


def write_section_markdown(node: SectionNode, output_dir: str) -> str | None:
    """立即写出单个章节 Markdown，返回路径；无可输出内容时返回 None。"""
    if node.get("is_answers") and node.get("answer_sections"):
        start_page = node["exercise_page_range"][0]
        fname = f"{start_page}_{_sanitize_filename(_markdown_output_title(node))}.md"
        path = os.path.join(output_dir, fname)
        content = node.get("content", {}).get("tool_review") or render_answer_markdown(node)
    elif node.get("has_exercise") and _numbered_exercises(node):
        start_page = node["exercise_page_range"][0]
        fname = f"{start_page}_{_sanitize_filename(_markdown_output_title(node))}.md"
        path = os.path.join(output_dir, fname)
        content = node.get("content", {}).get("tool_review") or render_exercise_markdown(node)
    else:
        return None
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def write_section_json(
    node: SectionNode,
    output_dir: str,
    *,
    path: str | None = None,
    document_name: str = "",
) -> str | None:
    """写出单个公共 section JSON；答案节点或空节点不写文件。"""
    public = public_section_from_node(
        node, path=path, document_name=document_name
    )
    if public is None:
        return None
    start_page = node["exercise_page_range"][0]
    # 节尾搜索标记只允许出现在 Markdown 文件名；公共 JSON 的文件名和
    # object.title 都使用原始 section 标题。
    fname = f"{start_page}_{_sanitize_filename(node['title'])}.json"
    output_path = os.path.join(output_dir, fname)
    os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(public, f, ensure_ascii=False, indent=2)
    return output_path


def write_outputs(
    sections: list[SectionNode],
    output_dir: str,
    markdown_path: str,
    *,
    document_name: str = "",
) -> Report:
    """导出结果文件并返回统计报告。

    - {起始页}_{章节}.md  每节一个 markdown，题号用 *N.* 行内斜体标记
    - {起始页}_{章节}.json  对应的扁平公共 section JSON
    - report.json     统计报告
    """
    os.makedirs(output_dir, exist_ok=True)

    def write_tree(node: SectionNode, parent_path: str = "") -> None:
        node_path = (
            f"{parent_path}/{node['title']}"
            if parent_path
            else node["title"]
        )
        write_section_markdown(node, output_dir)
        write_section_json(
            node,
            output_dir,
            path=node_path,
            document_name=document_name,
        )
        for child_node in node.get("children", []):
            write_tree(child_node, node_path)

    for section_node in sections:
        write_tree(section_node)

    report = collect_report(sections, markdown_path)
    with open(os.path.join(output_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return report


def print_summary(report: Report) -> None:
    """控制台摘要：总览 + 需要人工确认的章节（无习题 / 缺缓存页）。"""
    print(
        f"\n===== 提取报告：共 {report['total_exercises']} 道习题，"
        f"{len(report['sections'])} 个习题章节 ====="
    )
    problems = 0
    for sec in report["sections"]:
        if sec["exercise_count"] == 0:
            problems += 1
            print(
                f"  [无习题] {sec['path']}  "
                f"页 {sec['page_range'][0]}–{sec['page_range'][1] - 1}  "
                f"缓存 {sec['pages_cached']}/{sec['pages_total']}"
            )
        elif sec["pages_cached"] < sec["pages_total"]:
            print(
                f"  [缺页]   {sec['path']}  "
                f"缓存 {sec['pages_cached']}/{sec['pages_total']}  "
                f"已提取 {sec['exercise_count']} 道"
            )
    if problems == 0:
        print("  所有章节均有习题产出")
