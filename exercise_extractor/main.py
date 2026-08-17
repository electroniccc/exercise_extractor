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
    args = parser.parse_args()
    # --vl_server_url 是旧参数名，引擎统一走 vl_rec_server_url
    args.vl_rec_server_url = args.vl_server_url
    return args


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
