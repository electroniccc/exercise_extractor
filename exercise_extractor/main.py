"""主入口与 CLI（argparse）：解析参数后走 pipeline.run_pipeline。

安装后可用 `python -m exercise_extractor` 或 console script
`exercise-extractor` 调用，等价于仓库根目录的 `python main.py`。
"""

import argparse
import logging
import os

from .pipeline import run_pipeline
from .utils import compute_file_hash

logging.basicConfig(
    filename="main.log",
    level=logging.DEBUG,
    format="%(asctime)s %(name)s %(levelname)s:%(message)s",
    filemode="w",
)
logging.getLogger("toc_forge").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


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
        help="VL 模型名（备选: ATH-MaaS/OvisOCR2, PaddleOCR-VL-1.6-0.9B）",
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
    return parser.parse_args()


def main() -> None:
    """入口：解析参数并调用主流程。"""
    args = parse_args()
    # 只算缓存 hash 的调试入口：不跑目录识别，也不碰 OCR
    if args.hash:
        print(compute_file_hash(args.input))
        return
    run_pipeline(args, write_outputs_enabled=True, print_summary_enabled=True)


if __name__ == "__main__":
    main()
