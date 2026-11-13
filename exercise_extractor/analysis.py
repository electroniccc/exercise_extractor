"""Picklable, process-safe jobs for analyzing one prepared section.

Workers receive section text and immutable metadata only. They never access OCR
caches, write outputs, or mutate the parent's directory tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisJob:
    doc_type: str
    section: dict[str, Any]
    parent_title: str | None = None
    known_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisResult:
    patch: dict[str, Any]
    discarded: bool = False


def make_analysis_job(
    node: dict[str, Any],
    doc_type: str,
    *,
    parent_title: str | None = None,
    known_sections: set[str] | None = None,
) -> AnalysisJob:
    """Copy the fields needed for analysis, excluding children and shared state."""
    section = {
        key: value
        for key, value in node.items()
        if key not in {"children", "exercises", "answer_sections"}
    }
    section["children"] = []
    return AnalysisJob(
        doc_type=doc_type,
        section=section,
        parent_title=parent_title,
        known_sections=tuple(sorted(known_sections or ())),
    )


def analyze_section(job: AnalysisJob) -> AnalysisResult:
    """Analyze one detached section and return a patch for the parent to merge."""
    node = {**job.section, "children": []}
    sections = [node]
    if job.doc_type == "exercise_book":
        from .exercise_book import extract_exercises

        extract_exercises(sections)
    elif job.doc_type == "exampaper":
        from .exampaper import extract_exercises

        extract_exercises(sections)
    else:
        from .extraction import extract_exercises

        if job.parent_title:
            # The textbook analyzer uses the real parent title for implicit
            # section truncation and generic exercise-section labels.
            parent = {
                "title": job.parent_title,
                "has_exercise": False,
                "children": sections,
            }
            sections = [parent]
        extract_exercises(sections, known_sections=set(job.known_sections))
        if job.parent_title:
            retained = sections[0].get("children", []) if sections else []
            discarded = not any(item is node for item in retained) and bool(
                job.section.get("implicit")
            )
        else:
            discarded = not sections and bool(job.section.get("implicit"))

    # A one-node textbook analysis can discard an invalid implicit node by
    # removing it from the supplied list. Preserve that result explicitly.
    if job.doc_type != "textbook" and job.doc_type != "exampaper":
        discarded = not sections and bool(job.section.get("implicit"))
    elif job.doc_type == "exampaper":
        discarded = False
    patch = {
        key: value
        for key, value in node.items()
        if key not in {"title", "has_exercise", "exercise_page_range"}
        and (key != "children" or job.doc_type == "exampaper")
    }
    return AnalysisResult(patch=patch, discarded=discarded)


def merge_analysis_result(node: dict[str, Any], result: AnalysisResult) -> bool:
    """Apply a completed analysis patch; return whether this node is retained."""
    node.update(result.patch)
    return not result.discarded


def analyze_tree(sections: list[dict[str, Any]], doc_type: str) -> None:
    """Run the serial analysis path through the same detached worker interface."""
    from .parse_md_exercises import normalize_section

    known_sections: set[str] = set()

    def collect_titles(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node.get("title"):
                known_sections.add(normalize_section(node["title"]))
            collect_titles(node.get("children", []))

    if doc_type not in ("exercise_book", "exampaper"):
        collect_titles(sections)

    def walk(nodes: list[dict[str, Any]], parent_title: str | None = None) -> list[dict[str, Any]]:
        retained: list[dict[str, Any]] = []
        for node in nodes:
            if doc_type == "exampaper" and node.get("_exampaper_extracted"):
                retained.append(node)
                continue
            if node.get("has_exercise") and not node.get("is_answers"):
                result = analyze_section(
                    make_analysis_job(
                        node,
                        doc_type,
                        parent_title=parent_title,
                        known_sections=known_sections,
                    )
                )
                keep = merge_analysis_result(node, result)
            else:
                keep = True
            children = walk(node.get("children", []), node.get("title") or parent_title)
            node["children"] = children
            if keep:
                retained.append(node)
            else:
                # A discarded implicit leaf must not hide valid descendants.
                retained.extend(children)
        return retained

    sections[:] = walk(sections)
