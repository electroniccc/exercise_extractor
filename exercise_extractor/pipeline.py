"""提取管线的可复用编排：CLI（main.py）与对外 API（api.extract_pdf）共用。"""

import argparse
import logging
import os

from . import answers, exampaper, exercise_book
from .extraction import combine_section_pages, extract_exercises
from .html_tables import replace_html_tables
from .ocr import build_sections, create_ocr_pipeline, ocr_pages
from .oss import apply_oss_images, oss_config_from_args
from .parse_md_exercises import normalize_section
from .postprocess import print_summary, write_outputs
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
) -> list:
    """执行完整提取链，返回习题节点树（与 exercises.json 同结构）。

    args 需要字段：input / output / ocr_cache / model_dir / vl_server_url /
    vl_model / doc_type。output 为 None 时只做提取、不落盘输出文件。
    """
    if args.output:
        ensure_output_dir_empty(args.output)

    exercise_sections = build_sections(args)
    file_hash = compute_file_hash(args.input)
    logger.info(
        "filename: %s, hash: %s, output: %s",
        os.path.basename(args.input),
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
    combine_section_pages(exercise_sections, markdown_path)

    if args.doc_type == "exercise_book":
        exercise_book.extract_exercises(exercise_sections)
    elif args.doc_type == "exampaper":
        exampaper.extract_exercises(exercise_sections)
    else:
        extract_exercises(exercise_sections)

    # exampaper 自带答案/解析处理，不走通用答案章节匹配
    if args.doc_type != "exampaper":
        answers.extract_and_match(exercise_sections, markdown_path)

    # OSS/MinIO：把结果里的本地图片上传，引用替换为 OSS URL
    oss_cfg = oss_config_from_args(args)
    if oss_cfg:
        uploaded = apply_oss_images(exercise_sections, markdown_path, oss_cfg)
        logger.info("OSS 图片上传完成: %d 张", uploaded)

    if write_outputs_enabled:
        if not args.output:
            raise ValueError("write_outputs=True 时需要提供 output_dir")
        report = write_outputs(exercise_sections, args.output, markdown_path)
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

    exercise_sections = build_sections(args)
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

    # 2) 预计算全树 targets 与已知小节名，保证与整树一次提取行为一致
    targets = answers.collect_exercise_targets(exercise_sections)
    used_answer: set[int] = set()
    known_sections: set[str] = set()

    def collect_titles(nodes: list) -> None:
        for n in nodes:
            if n.get("title"):
                known_sections.add(normalize_section(n["title"]))
            collect_titles(n.get("children", []))

    collect_titles(exercise_sections)

    # 3) 按树序逐节 OCR → 拼页 → 切分 → 回填答案 → yield
    def walk(nodes: list, path: str):
        for node in nodes:
            node_path = f"{path} › {node['title']}" if path else node["title"]
            if node.get("is_answers"):
                if oss_cfg:
                    apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
                yield node
                yield from walk(node.get("children", []), node_path)
                continue
            if node.get("has_exercise"):
                ocr_pages(args, [node], markdown_path, pipeline)
                combine_section_pages([node], markdown_path)
                if args.doc_type == "exercise_book":
                    exercise_book.extract_exercises([node])
                elif args.doc_type == "exampaper":
                    exampaper.extract_exercises([node])
                else:
                    extract_exercises([node], known_sections=known_sections)
                target_index = next(
                    (j for j, t in enumerate(targets) if t["node"] is node),
                    None,
                )
                if target_index is not None:
                    answers.match_section_answers(
                        answer_sections_all, targets, target_index, used_answer
                    )
                if oss_cfg:
                    apply_oss_images([node], markdown_path, oss_cfg, oss_cache)
                yield node
            yield from walk(node.get("children", []), node_path)

    for section_node in exercise_sections:
        yield from walk([section_node], section_node["title"])

    # 4) 流式结束：清理临时挂载，按需落盘
    def cleanup(nodes: list) -> None:
        for n in nodes:
            n.pop("answer_section", None)
            cleanup(n.get("children", []))

    cleanup(exercise_sections)
    if write_outputs_enabled:
        write_outputs(exercise_sections, args.output, markdown_path)
