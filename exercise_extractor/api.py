"""对外公共 API：输入文件路径，返回提取到的习题 JSON。

返回结构与 exercises.json 一致（SectionNode 列表）。OCR 需要本地
llama.cpp server（默认 http://localhost:8080/v1）。当前支持 PDF，
后续计划支持 image / markdown 等输入。
"""

import os
from types import SimpleNamespace
from typing import Optional

from .pipeline import run_pipeline

DEFAULT_VL_SERVER_URL = "http://localhost:8080/v1"
DEFAULT_VL_MODEL = "PaddleOCR-VL-1.6-GGUF"

# 当前支持的输入类型；后续扩展 image / markdown
SUPPORTED_SOURCE_TYPES = ("pdf",)


def _resolve_model_dir(model_dir: Optional[str]) -> Optional[str]:
    """解析模型目录：显式参数优先，其次环境变量，最后 None（交给调用方判断）。

    toc_forge 需要显式模型目录，不内置任何机器路径；
    环境变量 EXERCISE_EXTRACTOR_MODEL_DIR 用于跨机器配置。
    """
    if model_dir:
        return model_dir
    return os.environ.get("EXERCISE_EXTRACTOR_MODEL_DIR")


def extract_exercise(
    source: str,
    source_type: str = "pdf",
    doc_type: str = "textbook",
    output_dir: Optional[str] = None,
    ocr_cache: str = ".ocr_cache",
    model_dir: Optional[str] = None,
    vl_server_url: str = DEFAULT_VL_SERVER_URL,
    vl_model: str = DEFAULT_VL_MODEL,
    write_outputs: bool = False,
) -> list:
    """提取 PDF 中的习题，返回 JSON 兼容的节点树。

    source：输入文件路径（当前仅支持 PDF）。
    source_type：输入类型，当前仅 "pdf"；后续支持 "image"/"markdown"。
    doc_type：textbook / exercise_book / exampaper。
    model_dir：模型目录；为 None 时读环境变量
    EXERCISE_EXTRACTOR_MODEL_DIR（toc_forge 需要显式模型目录）。
    write_outputs=True 时必须同时给 output_dir，会额外写出
    md / exercises.json / report.json；默认只返回数据不落盘。
    """
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise NotImplementedError(
            f"source_type={source_type!r} 暂不支持，当前仅支持 "
            f"{'/'.join(SUPPORTED_SOURCE_TYPES)}"
        )
    if not os.path.isfile(source):
        raise FileNotFoundError(source)
    if write_outputs and not output_dir:
        raise ValueError("write_outputs=True 时需要提供 output_dir")
    model_dir = _resolve_model_dir(model_dir)
    if not model_dir:
        raise ValueError(
            "需要提供 model_dir，或设置环境变量 EXERCISE_EXTRACTOR_MODEL_DIR"
        )
    args = SimpleNamespace(
        input=source,
        output=output_dir,
        ocr_cache=ocr_cache,
        model_dir=model_dir,
        vl_server_url=vl_server_url,
        vl_model=vl_model,
        doc_type=doc_type,
    )
    return run_pipeline(
        args, write_outputs_enabled=write_outputs, print_summary_enabled=False
    )


# 兼容旧名（v0.1.0 早期接口），后续可移除
extract_pdf = extract_exercise
