"""提取管线的可复用编排：CLI（main.py）与对外 API（api.extract_pdf）共用。"""

import argparse
import logging
import os

from . import answers, exampaper, exercise_book
from .extraction import combine_section_pages, extract_exercises
from .ocr import build_sections, create_ocr_pipeline, ocr_pages
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

    if write_outputs_enabled:
        if not args.output:
            raise ValueError("write_outputs=True 时需要提供 output_dir")
        report = write_outputs(exercise_sections, args.output, markdown_path)
        if print_summary_enabled:
            print_summary(report)
            print(f"输出目录: {os.path.abspath(args.output)}")
    return exercise_sections
