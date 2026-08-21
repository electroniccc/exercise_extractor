"""对外公共 API：输入文件路径，流式返回扁平的习题 section JSON。

每次 yield 一个不含 ``children`` 的公共 section object。OCR 需要本地
vllm-server（默认 http://localhost:8080/v1；llama.cpp+GGUF 已弃用）。
当前支持 PDF，
后续计划支持 image / markdown 等输入。
"""

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Optional

from .pipeline import iter_sections
from .public_schema import (
    public_answer_section_from_node,
    public_section_from_node,
)

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
    oss_endpoint: Optional[str] = None,
    oss_access_key: Optional[str] = None,
    oss_secret_key: Optional[str] = None,
    oss_bucket: Optional[str] = None,
    oss_region: Optional[str] = None,
    oss_prefix: str = "",
    oss_use_ssl: bool = False,
    oss_public_base_url: Optional[str] = None,
    vl_server_url: Optional[str] = None,
    vl_model: Optional[str] = None,
    write_outputs: bool = False,
) -> Iterator[dict]:
    """提取 PDF 中的习题，返回按节流式产出的公共 section 迭代器。

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
    oss_*：OSS/MinIO 配置。四个必填项（endpoint/access_key/secret_key/
    bucket）齐全时启用图片上传：把 markdown 里的本地图片传到 OSS，
    src 替换为公开 URL（优先 oss_public_base_url，否则
    http(s)://endpoint/bucket/对象键）；缺任一项则不启用。
    write_outputs=True 时必须同时给 output_dir，会额外写出每个 section 的
    md/json 以及 report.json；不再写总的 exercises.json。

    返回迭代器：每完成一个有编号习题的 section 就 yield 一个不含
    ``children`` 的公共 JSON object。section 的 ``path`` 使用 ``/`` 记录
    其在目录树中的完整路径；答案节点和无编号习题节点不会 yield。
    参数校验（文件存在、source_type、model_dir）在调用时立即执行。
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
        oss_endpoint=oss_endpoint,
        oss_access_key=oss_access_key,
        oss_secret_key=oss_secret_key,
        oss_bucket=oss_bucket,
        oss_region=oss_region,
        oss_prefix=oss_prefix,
        oss_use_ssl=oss_use_ssl,
        oss_public_base_url=oss_public_base_url,
        vl_model=vl_model,
        doc_type=doc_type,
    )
    internal_sections = iter_sections(args, write_outputs_enabled=write_outputs)
    document_name = os.path.splitext(os.path.basename(source))[0]

    def public_sections() -> Iterator[dict]:
        for node in internal_sections:
            if node.get("is_answers"):
                # 答案节点是供调用方传给 match_answers 的固定 JSON section，
                # 即使它没有 exercises 也必须保留。
                yield public_answer_section_from_node(
                    node,
                    path=node.get("_section_path") or node.get("title"),
                    document_name=document_name,
                )
                continue
            if not node.get("has_exercise"):
                continue
            public = public_section_from_node(
                node,
                path=node.get("_section_path") or node.get("title"),
                document_name=document_name,
            )
            if public is not None:
                yield public

    return public_sections()


# 兼容旧名（v0.1.0 早期接口），后续可移除
extract_pdf = extract_exercise
