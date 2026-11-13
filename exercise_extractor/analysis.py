"""Picklable, process-safe jobs for analyzing one prepared section.

Workers receive section text and immutable metadata only. They never access OCR
caches, write outputs, or mutate the parent's directory tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os
from typing import Any, Iterator


@dataclass(frozen=True)
class AnalysisJob:
    doc_type: str
    section: dict[str, Any]
    parent_title: str | None = None
    known_sections: tuple[str, ...] = ()
    path: str | None = None
    token: int | None = None
    part_index: int = 0
    part_count: int = 1


@dataclass(frozen=True)
class AnalysisResult:
    patch: dict[str, Any]
    discarded: bool = False


def default_analysis_workers() -> int:
    affinity_count = None
    if hasattr(os, "sched_getaffinity"):
        try:
            affinity_count = len(os.sched_getaffinity(0))
        except OSError:
            pass
    return min(4, affinity_count or os.cpu_count() or 1)


def validate_analysis_workers(workers: int | None) -> int:
    if workers is None:
        return default_analysis_workers()
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("analysis_workers must be a positive integer or None")
    return workers


def iter_analysis_results(jobs, workers: int | None = None):
    """Yield ``(job, result)`` in source order with bounded spawn workers."""
    workers = validate_analysis_workers(workers)
    if workers == 1:
        for job in jobs:
            try:
                yield job, analyze_section(job)
            except Exception as exc:
                raise RuntimeError(f"exercise analysis failed: {job.path or job.section.get('title')}") from exc
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    pending = deque()
    source = iter(jobs)
    window = workers * 2
    exhausted = False
    try:
        while pending or not exhausted:
            while len(pending) < window and not exhausted:
                try:
                    job = next(source)
                except StopIteration:
                    exhausted = True
                    break
                pending.append((job, executor.submit(analyze_section, job)))
            if pending:
                job, future = pending.popleft()
                try:
                    yield job, future.result()
                except Exception as exc:
                    for _, other in pending:
                        other.cancel()
                    raise RuntimeError(
                        f"exercise analysis failed: {job.path or job.section.get('title')}"
                    ) from exc
    finally:
        for _, future in pending:
            future.cancel()
        # Running workers cannot be forcibly stopped on Python 3.10; returning
        # promptly is preferable, and their small in-flight jobs finish without
        # being able to publish or yield anything.
        executor.shutdown(wait=False, cancel_futures=True)


def iter_analysis_events(events, workers: int | None = None):
    """Keep ordered non-analysis events alongside a bounded worker window."""
    workers = validate_analysis_workers(workers)
    if workers == 1:
        for event in events:
            if isinstance(event, AnalysisJob):
                try:
                    yield event, analyze_section(event)
                except Exception as exc:
                    raise RuntimeError(
                        f"exercise analysis failed: {event.path or event.section.get('title')}"
                    ) from exc
            else:
                yield event, None
        return

    executor = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
    )
    pending = deque()
    source = iter(events)
    window = workers * 2
    exhausted = False
    try:
        while pending or not exhausted:
            while len(pending) < window and not exhausted:
                try:
                    event = next(source)
                except StopIteration:
                    exhausted = True
                    break
                future = executor.submit(analyze_section, event) if isinstance(event, AnalysisJob) else None
                pending.append((event, future))
            if pending:
                event, future = pending.popleft()
                if future is None:
                    yield event, None
                    continue
                try:
                    yield event, future.result()
                except Exception as exc:
                    for _, other in pending:
                        if other is not None:
                            other.cancel()
                    raise RuntimeError(
                        f"exercise analysis failed: {event.path or event.section.get('title')}"
                    ) from exc
    finally:
        for _, future in pending:
            if future is not None:
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)


def make_analysis_job(
    node: dict[str, Any],
    doc_type: str,
    *,
    parent_title: str | None = None,
    known_sections: set[str] | None = None,
    path: str | None = None,
    token: int | None = None,
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
        path=path,
        token=token,
    )


def make_analysis_jobs(
    node: dict[str, Any],
    doc_type: str,
    *,
    parent_title: str | None = None,
    known_sections: set[str] | None = None,
    path: str | None = None,
    token: int | None = None,
) -> Iterator[AnalysisJob]:
    """Split single-root inputs only at their established semantic headings."""
    if doc_type == "exercise_book" and node.get("no_toc"):
        from .exercise_book import _no_toc_heading_chunks, _normalize_exercise_prefixes

        content = _normalize_exercise_prefixes(node.get("exercise_content", ""))
        chunk_count = sum(1 for _ in _no_toc_heading_chunks(content))
        if chunk_count:
            for index, (heading, chunk) in enumerate(_no_toc_heading_chunks(content)):
                yield AnalysisJob(
                    doc_type="exercise_book_heading",
                    section={"title": heading, "exercise_content": chunk},
                    path=f"{path}/{heading}" if path else heading,
                    token=token,
                    part_index=index,
                    part_count=chunk_count,
                )
            return
    if doc_type == "exampaper":
        from .exampaper import split_type_sections

        type_sections, _tail = split_type_sections(node.get("exercise_content", ""))
        if type_sections:
            for index, section in enumerate(type_sections):
                yield AnalysisJob(
                    doc_type="exampaper_type",
                    section={"type_section": section},
                    path=f"{path}/{section.get('title') or section.get('section_key') or index}" if path else str(section.get("title") or index),
                    token=token,
                    part_index=index,
                    part_count=len(type_sections),
                )
            return
    yield make_analysis_job(
            node,
            doc_type,
            parent_title=parent_title,
            known_sections=known_sections,
            path=path,
            token=token,
        )


def analyze_section(job: AnalysisJob) -> AnalysisResult:
    """Analyze one detached section and return a patch for the parent to merge."""
    if job.doc_type == "exercise_book_heading":
        from .exercise_book import _extract_no_toc_chunk

        return AnalysisResult(
            patch={
                "exercises": _extract_no_toc_chunk(
                    job.section["title"], job.section["exercise_content"]
                )
            }
        )
    if job.doc_type == "exampaper_type":
        from .exampaper import analyze_type_section

        return AnalysisResult(
            patch={"exercises": analyze_type_section(job.section["type_section"])}
        )
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


def analyze_tree(
    sections: list[dict[str, Any]], doc_type: str, analysis_workers: int | None = 1
) -> None:
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

    nodes_in_order: list[tuple[dict[str, Any], str | None, str]] = []

    def collect(nodes: list[dict[str, Any]], parent_title: str | None, path: str) -> None:
        for node in nodes:
            node_path = f"{path}/{node.get('title', '')}".strip("/")
            if doc_type != "exampaper" or not node.get("_exampaper_extracted"):
                if node.get("has_exercise") and not node.get("is_answers"):
                    nodes_in_order.append((node, parent_title, node_path))
                collect(node.get("children", []), node.get("title") or parent_title, node_path)

    collect(sections, None, "")

    def jobs():
        for token, (node, parent_title, path) in enumerate(nodes_in_order):
            yield from make_analysis_jobs(
                node,
                doc_type,
                parent_title=parent_title,
                known_sections=known_sections,
                path=path,
                token=token,
            )

    current_token: int | None = None
    current_parts: list[list[dict[str, Any]]] = []
    for job, result in iter_analysis_results(jobs(), analysis_workers):
        token = job.token or 0
        if current_token is not None and token != current_token:
            raise RuntimeError("analysis scheduler returned non-contiguous section parts")
        current_token = token
        node, _parent, path = nodes_in_order[token]
        try:
            if job.doc_type == "exercise_book_heading":
                if job.part_index == 0:
                    node["exercises"] = []
                node["exercises"].extend(result.patch.get("exercises", []))
                keep = True
            elif job.doc_type == "exampaper_type":
                current_parts.append(result.patch.get("exercises", []))
                keep = True
            else:
                keep = merge_analysis_result(node, result)
        except Exception as exc:
            raise RuntimeError(f"exercise analysis merge failed: {path}") from exc

        if job.part_index + 1 < job.part_count:
            continue
        if job.doc_type == "exampaper_type":
            from .exampaper import extract_exercises

            try:
                extract_exercises(
                    [node], precomputed_exercises_by_node={id(node): current_parts}
                )
            except Exception as exc:
                raise RuntimeError(f"exercise analysis merge failed: {path}") from exc
            current_parts = []
        node["_analysis_discarded"] = not keep
        current_token = None

    def prune(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        retained: list[dict[str, Any]] = []
        for node in nodes:
            children = prune(node.get("children", []))
            node["children"] = children
            if node.pop("_analysis_discarded", False):
                retained.extend(children)
            else:
                retained.append(node)
        return retained

    sections[:] = prune(sections)
