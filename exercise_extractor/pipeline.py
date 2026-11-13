"""提取管线的可复用编排：CLI 与公共 JSON API 共用。"""

import argparse
import logging
import os
import time

from . import answers, exampaper, exercise_book
from .analysis import (
    analyze_tree,
    estimate_analysis_workload_bytes,
    iter_analysis_events,
    make_analysis_jobs,
    select_analysis_workers,
)
from .extraction import combine_section_pages, extract_exercises
from .exercise_bbox import attach_exercise_bboxes
from .html_tables import replace_html_tables
from .ocr import build_sections, create_ocr_pipeline, ocr_pages
from .oss import apply_oss_images, oss_config_from_args
from .parse_md_exercises import normalize_section
from .postprocess import (
    print_summary,
    write_outputs,
    write_section_json,
    write_section_markdown,
)
from .utils import compute_file_hash

logger = logging.getLogger(__name__)


def ensure_output_dir_empty(output_dir: str) -> None:
    """输出目录非空/指向文件时提示并退出（不自动删除，避免误删已有结果）。"""
    if os.path.exists(output_dir) and not os.path.isdir(output_dir):
        print(
            f"输出路径 {os.path.abspath(output_dir)} 已存在且不是目录，"
            "请改用 --output 指定一个目录路径。"
        )
        raise SystemExit(1)
    if os.path.isdir(output_dir) and os.listdir(output_dir):
        entries = len(os.listdir(output_dir))
        print(
            f"输出目录 {os.path.abspath(output_dir)} 非空（{entries} 项）。"
            "为避免覆盖已有结果，请先清空该目录，"
            "或改用 --output 指定新的输出目录。"
        )
        raise SystemExit(1)


def run_pipeline(
    args: argparse.Namespace,
    write_outputs_enabled: bool = True,
    print_summary_enabled: bool = True,
    match_answers_enabled: bool = True,
) -> list:
    """执行完整提取链，返回内部习题节点树（仅供 CLI/适配层使用）。

    args 需要字段：input / source_type / output / ocr_cache / model_dir /
    vl_server_url / vl_model / doc_type。output 为 None 时只做提取、不落盘输出文件。
    """
    if args.output:
        ensure_output_dir_empty(args.output)

    section_build_started = time.perf_counter()
    logger.info("目录与习题区域识别开始: input=%s", os.path.basename(args.input))
    exercise_sections = build_sections(
        args,
        usage_callback=getattr(args, "llm_usage_callback", None),
    )
    logger.info("目录与习题区域识别完成: elapsed=%.1fs", time.perf_counter() - section_build_started)
    file_hash = compute_file_hash(args.input)
    logger.info(
        "filename: %s, hash: %s, output: %s",
        getattr(args, "document_name", os.path.splitext(os.path.basename(args.input))[0]),
        file_hash,
        args.output,
    )
    markdown_path = os.path.join(
        args.ocr_cache, "exercise_extractor", file_hash, "markdown"
    )
    os.makedirs(markdown_path, exist_ok=True)
    if args.output:
        os.makedirs(args.output, exist_ok=True)

    pipeline = create_ocr_pipeline(args)
    ocr_pages(args, exercise_sections, markdown_path, pipeline)
    stage_started = time.perf_counter()
    combine_section_pages(exercise_sections, markdown_path)
    logger.info("页面拼接完成: elapsed=%.3fs", time.perf_counter() - stage_started)

    stage_started = time.perf_counter()
    analyze_tree(
        exercise_sections,
        args.doc_type,
        analysis_workers=getattr(args, "analysis_workers", None),
        analysis_multiprocessing=getattr(args, "analysis_multiprocessing", False),
        analysis_threshold_bytes=(
            getattr(args, "analysis_threshold_mb", 8) * 1024 * 1024
        ),
    )
    logger.info("习题分析完成: elapsed=%.3fs", time.perf_counter() - stage_started)

    # exampaper 自带答案/解析处理，不走通用答案章节匹配。WebUI 在用户
    # 明确点击“匹配答案”前仍需提取答案 section 供答案 tab 展示，但关闭
    # 回填步骤，避免题目在用户操作前自动出现答案。
    if args.doc_type != "exampaper":
        stage_started = time.perf_counter()
        answers.extract_and_match(
            exercise_sections,
            markdown_path,
            match_enabled=match_answers_enabled,
        )
        logger.info("答案匹配完成: elapsed=%.3fs", time.perf_counter() - stage_started)

    # 使用 PaddleOCR-VL 原始 JSON 中的 parsing block，把题目映射回页面坐标。
    stage_started = time.perf_counter()
    attach_exercise_bboxes(exercise_sections, markdown_path)
    logger.info("习题 bbox 映射完成: elapsed=%.3fs", time.perf_counter() - stage_started)

    # OSS/MinIO：把结果里的本地图片上传，引用替换为 OSS URL
    oss_cfg = oss_config_from_args(args)
    if oss_cfg:
        uploaded = apply_oss_images(exercise_sections, markdown_path, oss_cfg)
        logger.info("OSS 图片上传完成: %d 张", uploaded)

    if write_outputs_enabled:
        if not args.output:
            raise ValueError("write_outputs=True 时需要提供 output_dir")
        report = write_outputs(
            exercise_sections,
            args.output,
            markdown_path,
            document_name=getattr(
                args, "document_name", os.path.splitext(os.path.basename(args.input))[0]
            ),
        )
        if print_summary_enabled:
            print_summary(report)
            print(f"输出目录: {os.path.abspath(args.output)}")
    return exercise_sections


def iter_sections(
    args: argparse.Namespace,
    write_outputs_enabled: bool = False,
):
    """流式提取：每完成一个习题章节就 yield 一个 SectionNode。

    课本/习题集章节多、整本 OCR 慢，按节处理可以让第一个章节完成后立即
    返回给调用方。答案节点页数少，先 OCR + 按小标题切块，随后逐节回填
    答案，保证流式返回的每个节点同样带 answer/solution_steps。
    CLI（run_pipeline）保持整树一次处理；对外 API 用本生成器。
    """
    if write_outputs_enabled:
        if not args.output:
            raise ValueError("write_outputs=True 时需要提供 output_dir")
        ensure_output_dir_empty(args.output)
        os.makedirs(args.output, exist_ok=True)

    section_build_started = time.perf_counter()
    logger.info("目录与习题区域识别开始: input=%s", os.path.basename(args.input))
    exercise_sections = build_sections(
        args,
        usage_callback=getattr(args, "llm_usage_callback", None),
    )
    logger.info("目录与习题区域识别完成: elapsed=%.1fs", time.perf_counter() - section_build_started)
    file_hash = compute_file_hash(args.input)
    logger.info("filename: %s, hash: %s", os.path.basename(args.input), file_hash)
    markdown_path = os.path.join(
        args.ocr_cache, "exercise_extractor", file_hash, "markdown"
    )
    os.makedirs(markdown_path, exist_ok=True)
    pipeline = create_ocr_pipeline(args)
    oss_cfg = oss_config_from_args(args)
    oss_cache: dict = {}

    # 1) 答案节点先行：页数少，先 OCR + 按小标题切块，供逐节回填
    answer_nodes: list = []

    def collect_answer_nodes(nodes: list) -> None:
        for n in nodes:
            if n.get("is_answers"):
                answer_nodes.append(n)
            collect_answer_nodes(n.get("children", []))

    collect_answer_nodes(exercise_sections)
    answer_sections_all: list = []
    for node in answer_nodes:
        ocr_pages(args, [node], markdown_path, pipeline)
        combine_section_pages([node], markdown_path)
        secs = answers.split_answer_sections(node.get("exercise_content", ""))
        for sec in secs:
            for item in sec.get("answers", []):
                for field in ("content", "solution_steps", "answer"):
                    text = item.get(field)
                    if text:
                        new_text = replace_html_tables(text)
                        if new_text != text:
                            item[field] = new_text
        node["answer_sections"] = secs
        answer_sections_all.extend(secs)

    # 2) 预计算已知小节名。公共 JSON API 在答案节点到达后由调用方统一
    # 调用 match_answers；CLI 仍在 run_pipeline 中做整树答案匹配。
    known_sections: set[str] = set()

    def collect_titles(nodes: list) -> None:
        for n in nodes:
            if n.get("title"):
                known_sections.add(normalize_section(n["title"]))
            collect_titles(n.get("children", []))

    collect_titles(exercise_sections)

    document_name = getattr(
        args, "document_name", os.path.splitext(os.path.basename(args.input))[0]
    )

    requested_workers = getattr(args, "analysis_workers", None)
    analysis_workers = select_analysis_workers(
        requested_workers,
        estimate_analysis_workload_bytes(exercise_sections, markdown_path),
        enabled=getattr(args, "analysis_multiprocessing", False),
        threshold_bytes=getattr(args, "analysis_threshold_mb", 8) * 1024 * 1024,
    )
    if analysis_workers > 1:
        nodes_by_token: dict[int, dict] = {}
        next_token = 0
        grouped_stream_results: dict[int, list] = {}

        def prepare_events(nodes: list, path: str, parent_title: str | None = None):
            nonlocal next_token
            for node in nodes:
                node_path = f"{path}/{node['title']}" if path else node["title"]
                node["_section_path"] = node_path
                if node.get("is_answers"):
                    yield ("answer", node, node_path)
                    yield from prepare_events(
                        node.get("children", []), node_path, node.get("title")
                    )
                    continue
                if node.get("has_exercise"):
                    ocr_pages(args, [node], markdown_path, pipeline)
                    combine_section_pages([node], markdown_path)
                    token = next_token
                    next_token += 1
                    nodes_by_token[token] = node
                    yield from make_analysis_jobs(
                        node,
                        args.doc_type,
                        parent_title=parent_title,
                        known_sections=known_sections,
                        path=node_path,
                        token=token,
                    )
                yield from prepare_events(
                    node.get("children", []), node_path, node.get("title")
                )

        def emit_node(node: dict, node_path: str):
            node["_section_path"] = node_path
            if not any(
                item.get("number") not in (None, "__preamble__")
                for item in node.get("exercises", [])
            ):
                return
            attach_exercise_bboxes([node], markdown_path)
            if oss_cfg:
                apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
            if write_outputs_enabled:
                write_section_markdown(node, args.output)
                write_section_json(
                    node,
                    args.output,
                    path=node_path,
                    document_name=document_name,
                )
            return node

        events = prepare_events(exercise_sections, "")
        ordered_results = iter_analysis_events(events, analysis_workers)
        try:
            for event, result in ordered_results:
                if isinstance(event, tuple) and event[0] == "answer":
                    _, node, node_path = event
                    if oss_cfg:
                        apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
                    if write_outputs_enabled:
                        write_section_markdown(node, args.output)
                    yield node
                    continue
                node = nodes_by_token[event.token]
                node_path = node.get("_section_path") or event.path or event.section.get("title", "")
                from .analysis import merge_analysis_result

                if event.doc_type == "exercise_book_heading":
                    if event.part_index == 0:
                        node["exercises"] = []
                    node["exercises"].extend(result.patch.get("exercises", []))
                    if event.part_index + 1 < event.part_count:
                        continue
                    keep = True
                elif event.doc_type == "exampaper_type":
                    parts = grouped_stream_results.setdefault(event.token, [])
                    parts.append(result.patch.get("exercises", []))
                    if event.part_index + 1 < event.part_count:
                        continue
                    grouped_stream_results.pop(event.token, None)
                    exampaper.extract_exercises(
                        [node],
                        precomputed_exercises_by_node={
                            id(node): parts
                        },
                    )
                    keep = True
                else:
                    keep = merge_analysis_result(node, result)
                if not keep:
                    logger.info("流式提取丢弃节点: title=%r path=%r", node.get("title"), node_path)
                    continue
                if args.doc_type == "exampaper" and node.get("_exampaper_extracted"):
                    for child in node.get("children", []):
                        child_path = f"{node_path}/{child['title']}"
                        emitted = emit_node(child, child_path)
                        if emitted is not None:
                            yield emitted
                    continue
                if not node.get("has_exercise"):
                    continue
                if not any(
                    item.get("number") not in (None, "__preamble__")
                    for item in node.get("exercises", [])
                ):
                    logger.info("流式提取过滤无编号习题节点: title=%r path=%r", node.get("title"), node_path)
                    continue
                emitted = emit_node(node, node_path)
                if emitted is not None:
                    yield emitted
        finally:
            ordered_results.close() if hasattr(ordered_results, "close") else None
            events.close() if hasattr(events, "close") else None
            def cleanup_parallel(nodes: list) -> None:
                for item in nodes:
                    item.pop("answer_section", None)
                    item.pop("_section_path", None)
                    item.pop("_exampaper_extracted", None)
                    cleanup_parallel(item.get("children", []))

            cleanup_parallel(exercise_sections)
        if write_outputs_enabled:
            write_outputs(
                exercise_sections,
                args.output,
                markdown_path,
                document_name=document_name,
            )
        return

    # 3) 按树序逐节 OCR → 拼页 → 切分 → 回填答案 → yield
    def walk(nodes: list, path: str):
        for node in nodes:
            node_path = f"{path}/{node['title']}" if path else node["title"]
            node["_section_path"] = node_path
            if node.get("is_answers"):
                if oss_cfg:
                    apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
                if write_outputs_enabled:
                    write_section_markdown(node, args.output)
                yield node
                yield from walk(node.get("children", []), node_path)
                continue
            if node.get("has_exercise"):
                ocr_pages(args, [node], markdown_path, pipeline)
                combine_section_pages([node], markdown_path)
                # 提取器可能会把无效的隐式节尾节点从传入列表中移除
                # （例如题号不连续的尾部正文）。流式流程必须检查这个结果，
                # 否则仍会继续使用原 node 写出并 yield，导致 WebUI 与 CLI
                # 的整树流程结果不一致。
                processed_nodes = [node]
                if args.doc_type == "exercise_book":
                    exercise_book.extract_exercises(processed_nodes)
                elif args.doc_type == "exampaper":
                    if not node.get("_exampaper_extracted"):
                        exampaper.extract_exercises(processed_nodes)
                else:
                    extract_exercises(processed_nodes, known_sections=known_sections)
                if not processed_nodes:
                    logger.info(
                        "流式提取丢弃节点: title=%r path=%r",
                        node.get("title"),
                        node_path,
                    )
                    # 当前隐式节点通常没有子节点；保留遍历逻辑，避免未来
                    # 节点被丢弃但仍挂有有效子节点时把子节点一并跳过。
                    yield from walk(node.get("children", []), node_path)
                    continue
                # 试卷提取会把整卷根节点拆成题型子节点；根节点仅作内部兼容，
                # 不应作为一个重复的公共 section 输出。
                if args.doc_type == "exampaper" and node.get("_exampaper_extracted") and not node.get("has_exercise"):
                    yield from walk(node.get("children", []), node_path)
                    continue
                # 与 CLI 的 write_section_markdown 保持同一过滤条件：只把
                # 至少含一道有效编号习题的节点交给公共 API。某些显式/隐式
                # 节点可能在切分后仍保留，但只含 preamble 或空题目；这类
                # 节点不能因为流式路径而出现在 WebUI 中。
                if not any(
                    item.get("number") not in (None, "__preamble__")
                    for item in node.get("exercises", [])
                ):
                    logger.info(
                        "流式提取过滤无编号习题节点: title=%r path=%r",
                        node.get("title"),
                        node_path,
                    )
                    yield from walk(node.get("children", []), node_path)
                    continue
                attach_exercise_bboxes([node], markdown_path)
                if oss_cfg:
                    apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
                if write_outputs_enabled:
                    write_section_markdown(node, args.output)
                    write_section_json(
                        node,
                        args.output,
                        path=node_path,
                        document_name=document_name,
                    )
                yield node
            yield from walk(node.get("children", []), node_path)

    yield from walk(exercise_sections, "")

    # 4) 流式结束：清理临时挂载，按需落盘
    def cleanup(nodes: list) -> None:
        for n in nodes:
            n.pop("answer_section", None)
            n.pop("_section_path", None)
            n.pop("_exampaper_extracted", None)
            cleanup(n.get("children", []))

    cleanup(exercise_sections)
    if write_outputs_enabled:
        write_outputs(
            exercise_sections,
            args.output,
            markdown_path,
            document_name=document_name,
        )
