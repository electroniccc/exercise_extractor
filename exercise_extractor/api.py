"""对外公共 API：输入文件路径，返回提取到的习题 JSON。

返回结构与 exercises.json 一致（SectionNode 列表）。OCR 需要本地
vllm-server（默认 http://localhost:8080/v1；llama.cpp+GGUF 已弃用）。
当前支持 PDF，
后续计划支持 image / markdown 等输入。
"""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Optional

from .pipeline import iter_sections

DEFAULT_VL_SERVER_URL = "http://localhost:8080/v1"
DEFAULT_VL_REC_BACKEND = "vllm-server"
DEFAULT_VL_REC_MAX_CONCURRENCY = 4
DEFAULT_VL_REC_API_MODEL_NAME = "PaddleOCR-VL-1.6-0.9B"

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
    vl_rec_backend: str = DEFAULT_VL_REC_BACKEND,
    vl_rec_server_url: str = DEFAULT_VL_SERVER_URL,
    vl_rec_max_concurrency: int = DEFAULT_VL_REC_MAX_CONCURRENCY,
    vl_rec_api_model_name: str = DEFAULT_VL_REC_API_MODEL_NAME,
    ocr_device: Optional[str] = None,
    ocr_engine: Optional[str] = None,
    toc_device: Optional[str] = None,
    toc_engine: Optional[str] = None,
    vl_server_url: Optional[str] = None,
    vl_model: Optional[str] = None,
    write_outputs: bool = False,
) -> Iterator[dict]:
    """提取 PDF 中的习题，返回按节流式产出的迭代器。

    source：输入文件路径（当前仅支持 PDF）。
    source_type：输入类型，当前仅 "pdf"；后续支持 "image"/"markdown"。
    doc_type：textbook / exercise_book / exampaper。
    model_dir：模型目录；为 None 时读环境变量
    EXERCISE_EXTRACTOR_MODEL_DIR（toc_forge 需要显式模型目录）。
    vl_rec_*：PaddleOCR-VL 引擎初始化参数（backend / server_url /
    并发数 / 引擎模型名）；vl_server_url、vl_model 为旧参数名的兼容别名，
    传入时覆盖对应的 vl_rec_* 配置。
    ocr_device / ocr_engine：PaddleOCR-VL 推理设备与引擎
    （如 device="gpu"/"cpu"，engine="paddle"/"onnxruntime"），
    None 时交给 paddlex 默认。
    toc_device / toc_engine：toc_forge 目录识别的设备与引擎
    （如 engine="onnxruntime" 使用模型目录下的 inference.onnx），
    None 时交给 toc_forge 默认。
    write_outputs=True 时必须同时给 output_dir，会额外写出
    md / exercises.json / report.json；默认只返回数据不落盘。

    返回迭代器：每完成一个习题章节就 yield 一个 SectionNode（与
    exercises.json 中节点同结构），课本/习题集章节多时不用等整本 OCR
    结束。参数校验（文件存在、source_type、model_dir）在调用时立即执行。
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
    if vl_server_url is not None:
        vl_rec_server_url = vl_server_url
    # vl_model 只控制输出后处理（含 "PaddleOCR" 时启用块公式修复），
    # 没显式给就用引擎模型名兜底
    vl_model = vl_model or vl_rec_api_model_name
    args = SimpleNamespace(
        input=source,
        output=output_dir,
        ocr_cache=ocr_cache,
        model_dir=model_dir,
        vl_rec_backend=vl_rec_backend,
        vl_rec_server_url=vl_rec_server_url,
        vl_rec_max_concurrency=vl_rec_max_concurrency,
        vl_rec_api_model_name=vl_rec_api_model_name,
        ocr_device=ocr_device,
        ocr_engine=ocr_engine,
        toc_device=toc_device,
        toc_engine=toc_engine,
        vl_model=vl_model,
        doc_type=doc_type,
    )
    return iter_sections(args, write_outputs_enabled=write_outputs)


# 兼容旧名（v0.1.0 早期接口），后续可移除
extract_pdf = extract_exercise
