"""主入口与 CLI（argparse）：解析参数后走 pipeline.run_pipeline。

安装后可用 `python -m exercise_extractor` 或 console script
`exercise-extractor` 调用，等价于仓库根目录的 `python main.py`。
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
from typing import Callable

from .parse_md_exercises import split_exercises
from .api import _prepare_source, extract_exercise_from_markdown
from .answers import match_answers
from .pipeline import ensure_output_dir_empty, run_pipeline
from .public_schema import public_section_from_node
from .postprocess import (
    print_summary,
    write_section_markdown,
    write_section_json,
    write_outputs,
    write_public_outputs,
)
from .review import (
    DEFAULT_REVIEW_MODEL,
    remove_review_statuses,
    review_basic,
    review_markdown_with_tools,
)
from .utils import compute_file_hash

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    # 本地调试默认模型目录；跨机器可设 EXERCISE_EXTRACTOR_MODEL_DIR 覆盖
    default_model_dir = (
        os.environ.get("EXERCISE_EXTRACTOR_MODEL_DIR")
        or r"/mnt/d/ai_models/paddle/official_models"
    )
    parser = argparse.ArgumentParser(
        prog="exercise_extractor",
        description="Exercise Extractor：从数学教材/习题集/试卷 PDF、图片或 Markdown 中提取习题",
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to PDF/image/Markdown；多张图片用逗号分隔",
    )
    parser.add_argument(
        "--output", default="output", type=str, help="Path to output directory"
    )
    parser.add_argument(
        "--ocr_cache",
        default=".ocr_cache",
        type=str,
        help="Path to OCR cache directory",
    )
    parser.add_argument(
        "--model_dir",
        type=str,
        default=default_model_dir,
        help=f"Path to model directory (default: {default_model_dir})",
    )
    parser.add_argument(
        "--vl_server_url",
        type=str,
        default="http://localhost:8080/v1",
        help="VL OCR 服务地址 (default: http://localhost:8080/v1)",
    )
    parser.add_argument(
        "--vl_model",
        type=str,
        default="PaddleOCR-VL-1.6-GGUF",
        help="输出后处理用模型名（含 PaddleOCR 时启用块公式修复；"
        "引擎模型名用 --vl_rec_api_model_name 指定）",
    )
    parser.add_argument(
        "--vl_rec_backend",
        type=str,
        default="vllm-server",
        help="VL OCR 引擎后端 (default: vllm-server)",
    )
    parser.add_argument(
        "--vl_rec_max_concurrency",
        type=int,
        default=4,
        help="VL OCR 最大并发数，需与服务端并行数一致 (default: 4)",
    )
    parser.add_argument(
        "--vl_rec_api_model_name",
        type=str,
        default="PaddleOCR-VL-1.6-0.9B",
        help="VL OCR 引擎模型名 (default: PaddleOCR-VL-1.6-0.9B)",
    )
    parser.add_argument(
        "--toc-vllm-name",
        type=str,
        default="qwen3.7-flash",
        help="toc_forge 目录识别模型名（默认 qwen3.7-flash）",
    )
    parser.add_argument(
        "--toc-api-base-url",
        type=str,
        default=None,
        help="toc_forge API 地址（默认读取 TOC_FORGE_API_BASE_URL 等环境变量）",
    )
    parser.add_argument(
        "--toc-api-key",
        type=str,
        default=None,
        help="toc_forge API key（默认读取 TOC_FORGE_API_KEY/ALIYUN_API_KEY）",
    )
    parser.add_argument(
        "--ocr-device",
        type=str,
        default=None,
        help="PaddleOCR-VL 推理设备（如 gpu/cpu，默认交给 paddlex）",
    )
    parser.add_argument(
        "--ocr-engine",
        type=str,
        default=None,
        help="PaddleOCR-VL 推理引擎（如 paddle/onnxruntime，默认交给 paddlex）",
    )
    parser.add_argument(
        "--oss-endpoint",
        type=str,
        default=None,
        help="OSS/MinIO 服务地址（如 127.0.0.1:9000）；缺任一项必填配置则不启用图片上传",
    )
    parser.add_argument("--oss-access-key", type=str, default=None, help="OSS Access Key")
    parser.add_argument("--oss-secret-key", type=str, default=None, help="OSS Secret Key")
    parser.add_argument("--oss-bucket", type=str, default=None, help="OSS 桶名")
    parser.add_argument("--oss-region", type=str, default=None, help="OSS region（可选）")
    parser.add_argument(
        "--oss-prefix", type=str, default="", help="OSS 对象键前缀（可选，默认空）"
    )
    parser.add_argument(
        "--oss-use-ssl",
        action="store_true",
        help="OSS 使用 https（MinIO 本地通常不开）",
    )
    parser.add_argument(
        "--oss-public-base-url",
        type=str,
        default=None,
        help="OSS 公开访问基础 URL（可选，默认 http(s)://endpoint/bucket）",
    )
    parser.add_argument(
        "--doc_type",
        type=str,
        default="textbook",
        choices=["textbook", "exercise_book", "exampaper"],
        help="文档类型: textbook 教材（小节末尾自动找习题）/ exercise_book 习题集 / exampaper 试卷",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="只计算并打印输入文件的缓存 hash，然后退出（不跑目录识别/OCR）",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="提取完成后用两轮文本 LLM 审校，并输出审校后的 Markdown",
    )
    parser.add_argument(
        "--review-base-url",
        type=str,
        default=None,
        help="审校 LLM 的 OpenAI-compatible base URL（默认与 toc_forge 相同）",
    )
    parser.add_argument(
        "--review-api-key",
        type=str,
        default=None,
        help="审校 LLM API key（默认读取 OPENAI_API_KEY 或 ALIYUN_API_KEY）",
    )
    parser.add_argument(
        "--review-model",
        type=str,
        default=DEFAULT_REVIEW_MODEL,
        help=f"审校 LLM 模型（默认 {DEFAULT_REVIEW_MODEL}）",
    )
    parser.add_argument(
        "--review-max-rounds",
        type=int,
        default=5,
        choices=[1, 2, 3, 4, 5],
        help="数学审校最多模型对话轮数（默认 5）",
    )
    parser.add_argument(
        "--review-with-tools",
        action="store_true",
        help="启用第二轮 SymPy 数学审校（默认关闭，仅执行基础审校）",
    )
    parser.add_argument(
        "--review-concurrency",
        type=int,
        default=5,
        help="小节审校的最大并发数（默认 5；服务端限流时可调低）",
    )
    args = parser.parse_args()
    # --vl_server_url 是旧参数名，引擎统一走 vl_rec_server_url
    args.vl_rec_server_url = args.vl_server_url
    return args


def _review_sections(
    sections: list,
    args: argparse.Namespace,
    on_section_reviewed: Callable[[dict], None] | None = None,
) -> None:
    """并发审校各习题节点，保存三版 Markdown 并把最终版切回 exercises。"""
    document_name = getattr(
        args, "document_name", os.path.splitext(os.path.basename(args.input))[0]
    )
    use_tools = bool(getattr(args, "review_with_tools", False))

    review_jobs: list[tuple[dict, str, dict]] = []

    def collect_review_jobs(node: dict, path: str) -> None:
        section_name = f"{path}/{node['title']}" if path else node["title"]
        if node.get("has_exercise") and any(
            item.get("number") not in (None, "__preamble__")
            for item in node.get("exercises", [])
        ):
            public = public_section_from_node(
                node,
                path=section_name,
                document_name=document_name,
            )
            if public is not None:
                review_jobs.append((node, section_name, public))
        for child in node.get("children", []):
            collect_review_jobs(child, section_name)

    def run_review_job(job: tuple[dict, str, dict]) -> tuple[dict, str, dict]:
        node, section_name, public_section = job
        basic_section = review_basic(
            public_section,
            base_url=args.review_base_url,
            api_key=args.review_api_key,
            model=args.review_model,
        )
        if use_tools:
            # 数学工具审校仍是保留中的实验分支；基础审校的公共 JSON
            # 结果先作为第二轮输入，最终再切回公共 section。
            basic_content = basic_section["content"]["basic_review"]
            final_content = review_markdown_with_tools(
                basic_content,
                document_name,
                section_name,
                base_url=args.review_base_url,
                api_key=args.review_api_key,
                model=args.review_model,
                max_rounds=args.review_max_rounds,
            )
            basic_section["content"]["tool_review"] = final_content
            tool_items = [
                item
                for item in split_exercises(
                    remove_review_statuses(final_content),
                    check_section_end=False,
                )
                if item.get("number") != "__preamble__"
            ]
            if not tool_items:
                raise ValueError("数学审校返回的 Markdown 无法解析出编号习题")
            basic_items = basic_section["exercises"]
            if len(tool_items) != len(basic_items):
                basic_section.setdefault("review_warnings", []).append(
                    {
                        "code": "tool_review_exercise_count_changed",
                        "original": len(basic_items),
                        "reviewed": len(tool_items),
                        "severity": "high",
                    }
                )
            for index, item in enumerate(tool_items):
                if index >= len(basic_items):
                    continue
                for field in (
                    "pages",
                    "question_type",
                    "section",
                    "section_key",
                    "original_number",
                ):
                    item[field] = basic_items[index].get(field)
            basic_section["exercises"] = tool_items
        return node, section_name, basic_section

    def apply_review_result(
        node: dict,
        section_name: str,
        reviewed_section: dict,
    ) -> None:
        node["content"] = reviewed_section["content"]
        node["review_warnings"] = reviewed_section.get("review_warnings", [])
        node["answer_match"] = reviewed_section.get("answer_match", {})
        node["_section_path"] = section_name
        # review_basic 已完成固定字段归一化、答案掩码清理和质量告警；即使
        # 题数变化也直接采用模型结果，不再把内部 exercises 回退为原文。
        node["exercises"] = reviewed_section["exercises"]

    for section in sections:
        collect_review_jobs(section, "")
    if not review_jobs:
        return
    concurrency = getattr(args, "review_concurrency", 5)
    if concurrency < 1:
        raise ValueError("review_concurrency 必须是正整数")
    logger.info(
        "开始并发审校：小节数=%d，并发数=%d，数学工具审校=%s",
        len(review_jobs),
        concurrency,
        "启用" if use_tools else "关闭",
    )
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(run_review_job, job) for job in review_jobs]
        for future in as_completed(futures):
            node, section_name, reviewed_section = future.result()
            apply_review_result(node, section_name, reviewed_section)
            if on_section_reviewed is not None:
                on_section_reviewed(node)
    logger.info("并发审校完成：小节数=%d", len(review_jobs))


def _run_main(args: argparse.Namespace) -> None:
    """使用已规范化的输入参数调用主流程。"""
    if args.review:
        # 审校涉及外部模型调用，默认打开 INFO 日志，让 CLI 能看到当前章节和轮次。
        # 若调用方已经配置 logging.basicConfig，则不会覆盖其配置。
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    # 只算缓存 hash 的调试入口：不跑目录识别，也不碰 OCR
    if args.hash:
        print(compute_file_hash(args.input))
        return
    if getattr(args, "source_type", None) == "markdown":
        _run_markdown_cli(args)
        return
    if not args.review:
        run_pipeline(args, write_outputs_enabled=True, print_summary_enabled=True)
        return

    file_hash = compute_file_hash(args.input)
    markdown_path = os.path.join(
        args.ocr_cache, "exercise_extractor", file_hash, "markdown"
    )
    # 审校必须发生在原有提取完成之后；每完成一个小节立即落盘 Markdown。
    sections = run_pipeline(
        args, write_outputs_enabled=False, print_summary_enabled=False
    )
    def write_reviewed_section(node: dict) -> None:
        path = write_section_markdown(node, args.output)
        section_path = node.get("_section_path")
        if section_path is None:
            section_path = node.get("title", "")
        write_section_json(
            node,
            args.output,
            path=section_path,
            document_name=args.document_name,
        )
        if path:
            logger.info("审校小节已实时写出：%s", path)

    _review_sections(sections, args, on_section_reviewed=write_reviewed_section)
    report = write_outputs(
        sections,
        args.output,
        markdown_path,
        document_name=args.document_name,
    )
    print_summary(report)
    print(f"输出目录: {os.path.abspath(args.output)}")


def _run_markdown_cli(args: argparse.Namespace) -> None:
    """Markdown CLI 分支：无 OCR 地切分、匹配答案并导出公共 section JSON。"""
    with open(args.input, encoding="utf-8") as f:
        markdown = f.read()
    sections = list(
        extract_exercise_from_markdown(
            markdown,
            doc_type=args.doc_type,
            document_name=args.document_name,
            title=args.document_name,
        )
    )

    # 与 PDF 流程一致：答案 section 是数据源，不作为普通题目；依次回填到
    # 当前 Markdown 内已识别出的习题 section。
    exercise_sections = [section for section in sections if not section.get("is_answers")]
    for answer_section in (section for section in sections if section.get("is_answers")):
        exercise_sections = match_answers(exercise_sections, answer_section)
    iterator = iter(exercise_sections)
    sections = [next(iterator) if not section.get("is_answers") else section for section in sections]

    if args.review:
        if args.review_with_tools:
            logger.warning("Markdown CLI 暂不执行 SymPy 审校，仅执行基础审校")
        reviewed_sections: list[dict] = []
        for section in sections:
            if section.get("is_answers"):
                reviewed_sections.append(section)
                continue
            reviewed_sections.append(
                review_basic(
                    section,
                    base_url=args.review_base_url,
                    api_key=args.review_api_key,
                    model=args.review_model,
                )
            )
        sections = reviewed_sections

    report = write_public_outputs(sections, args.output)
    print_summary(report)
    print(f"输出目录: {os.path.abspath(args.output)}")


def main() -> None:
    args = parse_args()
    if not args.hash and args.output:
        # 保持输出目录保护在输入规范化/哈希计算之前生效，避免输入路径
        # 错误时绕过对已有输出的保护。
        ensure_output_dir_empty(args.output)
    raw_sources = args.input
    raw_source_paths = [path.strip() for path in raw_sources.split(",")]
    if len(raw_source_paths) == 1 and raw_source_paths[0].lower().endswith(".md"):
        markdown_input = raw_source_paths[0]
        if not os.path.isfile(markdown_input):
            raise FileNotFoundError(2, "No such file or directory", markdown_input)
        args.input = markdown_input
        args.source_type = "markdown"
        args.document_name = os.path.splitext(os.path.basename(markdown_input))[0]
        _run_main(args)
        return
    prepared_source, source_type, cleanup_path = _prepare_source(
        raw_sources, args.ocr_cache
    )
    args.input = prepared_source
    args.source_type = source_type
    args.document_name = os.path.splitext(os.path.basename(raw_source_paths[0]))[0]
    try:
        _run_main(args)
    finally:
        if cleanup_path:
            try:
                os.unlink(cleanup_path)
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
