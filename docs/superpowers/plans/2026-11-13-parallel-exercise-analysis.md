# Parallel Exercise Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speed up CPU-heavy exercise analysis on large documents while preserving extraction results and order.

**Architecture:** Keep OCR and shared tree updates in the parent process. Send independent section or type-section text to a bounded `spawn` process pool, then merge results in source order. Preserve a sequential mode and avoid unsafe page-based splitting.

**Tech Stack:** Python >=3.10, `concurrent.futures.ProcessPoolExecutor`, existing `test.smoke_test` suite.

**Spec:** `docs/superpowers/specs/2026-11-13-parallel-exercise-analysis-design.md`

## Global Constraints

- Keep public section and Exercise JSON fields unchanged.
- Preserve directory order, exercise numbering, answers, page ranges, bbox, and Markdown output.
- Do not run PaddleOCR, OSS, or shared OCR cache writes in analysis workers.
- Multiprocessing is disabled by default and requires `analysis_multiprocessing=True` / `--analysis-multiprocessing`.
- When enabled, use the process pool only when estimated document size meets `analysis_threshold_mb` / `--analysis-threshold-mb` (default 8 MiB).
- Worker count defaults to `min(4, available CPUs)`; `analysis_workers=1` forces sequential analysis.
- Bound in-flight analysis tasks to at most `2 * analysis_workers`.
- Work only at proven section, heading, or question-type boundaries; do not split by page or fixed text size.

## Review Focus

- A textbook implicit section is discarded in both worker modes, including when its children must remain reachable.
- Two adjacent sections sharing a boundary page keep the same questions and page metadata.
- A worker exception identifies the section and stops further output instead of becoming an empty section.
- A streaming caller that closes early releases the pool and does not yield more sections.
- An exampaper with an answer tail matches answers after parallel type-section parsing.

---

### Task 1: Baseline and cheap concatenation fix

**Files:** Modify `exercise_extractor/extraction.py`, `exercise_extractor/pipeline.py`; test in `test/smoke_test/unit.py` or a focused `test/test_scenarios/*.txt` scenario.

**Interfaces:** Existing `combine_section_pages(sections, markdown_path) -> None` remains unchanged.

- [ ] Add a focused regression that checks exact combined Markdown and page markers across cached pages; run `python -m test.smoke_test` to see the baseline.
- [ ] Change `combine_section_pages` from repeated string `+=` to list accumulation and one `join`, preserving exact output.
- [ ] Add stage timing logs around combine, analysis, answer matching, and bbox in `run_pipeline`; keep them out of public JSON.
- [ ] Run `python -m test.smoke_test`; commit this self-contained change.

### Task 2: Pure section analysis seam

**Files:** Modify `exercise_extractor/extraction.py`, `exercise_extractor/exercise_book.py`, `exercise_extractor/exampaper.py`; create `exercise_extractor/analysis.py` if it gives a small, import-safe worker entry point; test in `test/smoke_test/unit.py`.

**Interfaces:** Define a picklable `AnalysisJob` and `AnalysisResult` in `analysis.py`; `analyze_section(job: AnalysisJob) -> AnalysisResult` accepts only one section's text and required immutable metadata. Parent-side merge applies a result to the existing node, including implicit discard. The sequential caller uses the same function.

- [ ] Add baseline equality tests for textbook explicit/implicit nodes, exercise-book nodes, and exampaper type sections; include the five Review Focus cases owned here.
- [ ] Extract current per-node transformations from recursive tree mutation into `analyze_section`; pass parent title and known-section names explicitly.
- [ ] Keep global answer matching, bbox, OSS, and output outside the worker; preserve current sequential behavior with `analysis_workers=1`.
- [ ] Run `python -m test.smoke_test`; commit.

### Task 3: Bounded process pool for CLI and public API

**Files:** Modify `exercise_extractor/pipeline.py`, `exercise_extractor/main.py`, `exercise_extractor/api.py`; use `analysis.py`; test in `test/smoke_test/unit.py`.

**Interfaces:** Add CLI `--analysis-multiprocessing`, `--analysis-workers`, `--analysis-threshold-mb` and API parameters `analysis_multiprocessing: bool = False`, `analysis_workers: int | None = None`, `analysis_threshold_mb: int = 8`. Pool creation requires opt-in and workload above threshold. Internal scheduler returns results in source order, limits submitted futures to `2 * workers`, and uses `multiprocessing.get_context("spawn")`.

- [ ] Add tests comparing worker counts 1 and 2 for the same cached section tree, including ordering, a worker failure, and early generator close.
- [ ] Implement pool scheduling and ordered merge in `run_pipeline`; preserve removal of implicit nodes and descendants.
- [ ] Extend `iter_sections` with bounded lookahead: main process OCRs and combines sections in tree order, workers analyze, parent yields in tree order; ensure `finally` shuts down the pool when iteration closes.
- [ ] Wire and validate the CLI/API parameter without changing public JSON fields.
- [ ] Run `python -m test.smoke_test`; commit.

### Task 4: Single-root documents and performance evidence

**Files:** Modify `exercise_extractor/exercise_book.py`, `exercise_extractor/exampaper.py`, `exercise_extractor/pipeline.py`; test in `test/smoke_test/unit.py` and existing scenarios.

**Interfaces:** Split no-TOC exercise books only at the heading boundaries already used by `_extract_no_toc`; split exampapers only at `split_type_sections` boundaries. Main process retains cross-section numbering and answer-tail matching.

- [ ] Add equality tests for 1 versus 2 workers on a multi-heading no-TOC exercise book and an exampaper with answer tail.
- [ ] Add independent per-heading and per-type-section jobs, then merge in original order; if no safe split exists, use the sequential path.
- [ ] Run `python -m test.smoke_test`, inspect exact output equality, and record elapsed analysis time and peak memory for workers 1, 2, and 4 on any available fully cached large PDF. If no such PDF is available, report that benchmark as unavailable rather than inventing a speedup.
- [ ] Commit implementation and report measured results and limitations.
