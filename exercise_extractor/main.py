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
from .pipeline import run_pipeline
from .postprocess import (
    print_summary,
    render_exercise_markdown,
    write_section_markdown,
    write_outputs,
)
from .review import (
    DEFAULT_REVIEW_MODEL,
    remove_review_statuses,
    review_markdown_basic,
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
        description="Exercise Extractor：从数学教材/习题集/试卷 PDF 中提取习题",
    )
    parser.add_argument(
        "--input", type=str, required=True, help="Path to input pdf file"
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
        "--toc-device",
        type=str,
        default=None,
        help="toc_forge 目录识别设备（如 gpu/cpu，默认交给 toc_forge）",
    )
    parser.add_argument(
        "--toc-engine",
        type=str,
        default=None,
        help="toc_forge 目录识别引擎（如 onnxruntime 使用 inference.onnx）",
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


def _copy_review_metadata(original: dict, reviewed: dict) -> None:
    """重新切分审校 Markdown 后，恢复 OCR 阶段产生的元数据。"""
    for field in (
        "pages",
        "question_type",
        "section_key",
        "original_number",
        "type_index",
    ):
        if field in original:
            reviewed[field] = original[field]


def _review_sections(
    sections: list,
    args: argparse.Namespace,
    on_section_reviewed: Callable[[dict], None] | None = None,
) -> None:
    """并发审校各习题节点，保存三版 Markdown 并把最终版切回 exercises。"""
    document_name = os.path.splitext(os.path.basename(args.input))[0]
    use_tools = bool(getattr(args, "review_with_tools", False))

    review_jobs: list[tuple[dict, str, str]] = []

    def collect_review_jobs(node: dict, path: str) -> None:
        section_name = f"{path} › {node['title']}" if path else node["title"]
        if node.get("has_exercise") and node.get("exercises"):
            review_jobs.append((node, section_name, render_exercise_markdown(node)))
        for child in node.get("children", []):
            collect_review_jobs(child, section_name)

    def run_review_job(job: tuple[dict, str, str]) -> tuple[dict, str, str, str, str]:
        node, section_name, original_content = job
        basic_content = review_markdown_basic(
            original_content,
            document_name,
            section_name,
            base_url=args.review_base_url,
            api_key=args.review_api_key,
            model=args.review_model,
        )
        final_content = basic_content
        if use_tools:
            final_content = review_markdown_with_tools(
                basic_content,
                document_name,
                section_name,
                base_url=args.review_base_url,
                api_key=args.review_api_key,
                model=args.review_model,
                max_rounds=args.review_max_rounds,
            )
        return node, section_name, original_content, basic_content, final_content

    def apply_review_result(
        node: dict,
        section_name: str,
        original_content: str,
        basic_content: str,
        final_content: str,
    ) -> None:
        node["content"] = {
            "original": original_content,
            "basic_review": basic_content,
            "tool_review": final_content,
        }
        reviewed_items = split_exercises(
            remove_review_statuses(final_content),
            check_section_end=False,
        )
        original_items = node["exercises"]
        reviewed_numbered = [
            item for item in reviewed_items if item.get("number") != "__preamble__"
        ]
        original_numbered = [
            item for item in original_items if item.get("number") != "__preamble__"
        ]
        if len(reviewed_numbered) == len(original_numbered):
            for old, new in zip(original_numbered, reviewed_numbered):
                _copy_review_metadata(old, new)
            node["exercises"] = reviewed_items
        else:
            logger.warning("[%s] 审校后题数变化，保留原 exercises 结构", section_name)

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
            (
                node,
                section_name,
                original_content,
                basic_content,
                final_content,
            ) = future.result()
            apply_review_result(
                node, section_name, original_content, basic_content, final_content
            )
            if on_section_reviewed is not None:
                on_section_reviewed(node)
    logger.info("并发审校完成：小节数=%d", len(review_jobs))


def main() -> None:
    """入口：解析参数并调用主流程。"""
    args = parse_args()
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
        if path:
            logger.info("审校小节已实时写出：%s", path)

    _review_sections(sections, args, on_section_reviewed=write_reviewed_section)
    report = write_outputs(sections, args.output, markdown_path)
    print_summary(report)
    print(f"输出目录: {os.path.abspath(args.output)}")


if __name__ == "__main__":
    main()
