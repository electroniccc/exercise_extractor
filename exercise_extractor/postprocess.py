"""提取后的后处理：统计报告、导出 markdown / JSON、控制台摘要。

collect_report：汇总各习题章节的提取情况（页码范围、缓存页数、习题数）；
write_outputs：导出每节一个 markdown、exercises.json、report.json；
print_summary：控制台摘要。
"""

import json
import logging
import os
import re
from typing import NotRequired, TypedDict

from .extraction import SectionNode

logger = logging.getLogger(__name__)


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
                        "path": path,
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
                exercises = node.get("exercises", [])
                report["sections"].append(
                    {
                        "title": node["title"],
                        "path": path,
                        "page_range": [start_page, end_page],
                        "pages_total": len(pages),
                        "pages_cached": len(cached),
                        "exercise_count": len(exercises),
                        "numbers": [e["number"] for e in exercises],
                    }
                )
                report["total_exercises"] += len(exercises)
        for child_node in node.get("children", []):
            walk(child_node, f"{path} › {node['title']}" if path else node["title"])

    for section_node in sections:
        walk(section_node)
    return report


def write_outputs(
    sections: list[SectionNode], output_dir: str, markdown_path: str
) -> Report:
    """导出结果文件并返回统计报告。

    - {起始页}_{章节}.md  每节一个 markdown，题号用 *N.* 行内斜体标记
    - exercises.json  完整结果（含 exercise_content 和 exercises）
    - report.json     统计报告
    """
    os.makedirs(output_dir, exist_ok=True)

    def sanitize_filename(title: str) -> str:
        return re.sub(r'[\\/:*?"<>|\s]+', "_", title).strip("_")

    def write_section_markdown(node: SectionNode) -> None:
        # 习题答案章节：按小标题分块输出，附匹配信息注释
        if node.get("is_answers") and node.get("answer_sections"):
            start_page = node["exercise_page_range"][0]
            fname = f"{start_page}_{sanitize_filename(node['title'])}.md"
            with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
                for sec in node["answer_sections"]:
                    f.write(f"## {sec['title']}\n\n")
                    if sec.get("matched_title"):
                        f.write(
                            f"<!-- 匹配：{sec.get('matched_path') or sec['matched_title']}"
                            f"（答案 {len(sec['answers'])} 题 vs 习题 "
                            f"{sec.get('matched_count')} 题）-->\n\n"
                        )
                    for item in sec["answers"]:
                        sep = item.get("separator", ".")
                        f.write(f"*{item['number']}{sep}* {item['content']}\n\n")
            return
        if node.get("has_exercise") and node.get("exercises"):
            start_page = node["exercise_page_range"][0]
            fname = f"{start_page}_{sanitize_filename(node['title'])}.md"
            with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as f:
                last_section = None
                title_norm = re.sub(r"\s+", "", node["title"])
                for item in node["exercises"]:
                    if item["number"] == "__preamble__":
                        f.write(f"{item['content']}\n\n")
                        continue
                    # 所属小节（如整章答案里每组答案前的小节名）与文件名重复时不写；
                    # 目录标题可能带空格而页内标题不带（“习题 2-2” vs “习题2-2”），比较时忽略空白
                    section = item.get("section")
                    section_norm = re.sub(r"\s+", "", section) if section else ""
                    if (
                        section
                        and section_norm != title_norm
                        and section_norm != last_section
                    ):
                        f.write(f"## {section}\n\n")
                        last_section = section_norm
                    sep = item.get("separator", ".")
                    f.write(f"*{item['number']}{sep}* {item['content']}\n\n")
                    # 解答过程/答案单独成段输出（与切分前格式一致）
                    if item.get("solution_steps"):
                        f.write(f"解：{item['solution_steps']}\n\n")
                    if item.get("answer"):
                        f.write(f"答：{item['answer']}\n\n")
        for child_node in node.get("children", []):
            write_section_markdown(child_node)

    for section_node in sections:
        write_section_markdown(section_node)

    with open(os.path.join(output_dir, "exercises.json"), "w", encoding="utf-8") as f:
        json.dump(sections, f, ensure_ascii=False, indent=2)

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
                f"  [无习题] {sec['path']} › {sec['title']}  "
                f"页 {sec['page_range'][0]}–{sec['page_range'][1] - 1}  "
                f"缓存 {sec['pages_cached']}/{sec['pages_total']}"
            )
        elif sec["pages_cached"] < sec["pages_total"]:
            print(
                f"  [缺页]   {sec['path']} › {sec['title']}  "
                f"缓存 {sec['pages_cached']}/{sec['pages_total']}  "
                f"已提取 {sec['exercise_count']} 道"
            )
    if problems == 0:
        print("  所有章节均有习题产出")
