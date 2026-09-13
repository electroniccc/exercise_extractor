"""对外公共 API：输入 PDF、图片或 Markdown，流式返回扁平习题 section JSON。

每次 yield 一个不含 ``children`` 的公共 section object。OCR 需要本地
vllm-server（默认 http://localhost:8080/v1；llama.cpp+GGUF 已弃用）。
``extract_exercise`` 支持 PDF 和一张或多张图片；Markdown 使用独立的
``extract_exercise_from_markdown`` 入口。
"""

import os
import hashlib
import re
import tempfile
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Optional

import pymupdf

from . import answers, exampaper
from .exercise_book import _normalize_exercise_prefixes
from .extraction import attach_figures, convert_exercise_tables, extract_figures
from .pipeline import iter_sections
from .parse_md_exercises import HEADING_RE, split_exercises, split_solutions_for_section
from .public_schema import (
    public_answer_section_from_node,
    public_section_from_node,
)
from .toc_detect import is_answers_title

DEFAULT_VL_SERVER_URL = "http://localhost:8080/v1"
DEFAULT_VL_REC_BACKEND = "vllm-server"
DEFAULT_VL_REC_MAX_CONCURRENCY = 4
DEFAULT_VL_REC_API_MODEL_NAME = "PaddleOCR-VL-1.6-0.9B"

# ``extract_exercise`` 的文件输入类型；Markdown 是独立的字符串入口。
SUPPORTED_SOURCE_TYPES = ("pdf", "image")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _image_group_hash(paths: list[str]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        size = os.path.getsize(path)
        digest.update(size.to_bytes(8, "big"))
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
    return digest.hexdigest()[:32]


def _prepare_source(
    source: str | list[str], cache_dir: str = ".ocr_cache"
) -> tuple[str, str, str]:
    """校验输入并把图片序列整理成可复用的缓存 PDF。"""
    if isinstance(source, str):
        paths = [path.strip() for path in source.split(",")] if "," in source else [source]
    else:
        paths = list(source)
    if not paths or not all(isinstance(path, str) for path in paths):
        raise TypeError("source 必须是 PDF/图片路径字符串，或图片路径列表")
    for path in paths:
        if not os.path.isfile(path):
            raise FileNotFoundError(2, "No such file or directory", path)
    if len(paths) == 1 and os.path.splitext(paths[0])[1].lower() == ".pdf":
        return paths[0], "pdf", ""
    if not all(os.path.splitext(path)[1].lower() in _IMAGE_SUFFIXES for path in paths):
        raise ValueError("source 只能是 PDF，或全部为 png/jpg/jpeg/webp/bmp/tif 图片")
    group_hash = _image_group_hash(paths)
    image_pdf_dir = os.path.join(cache_dir, "exercise_extractor", "image_pdf")
    os.makedirs(image_pdf_dir, exist_ok=True)
    cached_pdf = os.path.join(image_pdf_dir, f"{group_hash}.pdf")
    if os.path.isfile(cached_pdf) and os.path.getsize(cached_pdf) > 0:
        return cached_pdf, "image", ""
    temp = tempfile.NamedTemporaryFile(
        prefix=f".{group_hash}_", suffix=".pdf", dir=image_pdf_dir, delete=False
    )
    temp_path = temp.name
    temp.close()
    os.unlink(temp_path)
    output = pymupdf.open()
    try:
        for path in paths:
            pixmap = pymupdf.Pixmap(path)
            page = output.new_page(width=pixmap.width, height=pixmap.height)
            page.insert_image(page.rect, filename=path)
        output.save(temp_path)
        os.replace(temp_path, cached_pdf)
    finally:
        output.close()
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return cached_pdf, "image", ""


def reparse_exercises(section: dict, markdown: str | None = None) -> dict:
    """按 Markdown 重新拆分题目并返回完整固定 section JSON。

    该接口供调用方在人工编辑或 LLM 审校修复题号后重新建立 exercises。
    ``markdown`` 为空时使用 ``section.content.original``；题号支持普通、
    斜体和粗体 Markdown 标记（如 ``*1.*``、``**1.**``）。
    """
    if not isinstance(section, dict):
        raise TypeError("section 必须是 JSON object")
    result = dict(section)
    content = result.get("content")
    if not isinstance(content, dict):
        content = {}
    text = markdown if markdown is not None else content.get("original")
    if not isinstance(text, str):
        raise TypeError("section.content.original 必须是字符串")
    parsed = [
        item for item in split_exercises(text, check_section_end=False)
        if item.get("number") != "__preamble__"
    ]
    exercises = []
    for item in parsed:
        exercises.append({
            "number": str(item.get("number") or ""),
            "original_number": item.get("original_number"),
            "separator": item.get("separator") or ".",
            "content": item.get("content") or "",
            "solution_steps": item.get("solution_steps") or "",
            "answer": item.get("answer") or "",
            "question_type": item.get("question_type") or "short_answer",
            "section": item.get("section"),
            "section_key": item.get("section_key"),
            "pages": list(item.get("pages") or []),
            "bbox": list(item.get("bbox") or []),
        })
    result["exercises"] = exercises
    result["exercise_count"] = len(exercises)
    result["content"] = {**content, "original": text}
    return result


def _renumber_markdown_exercises(exercises: list[dict]) -> None:
    """公共 Markdown 入口与 PDF 流程一样：按 section 内顺序重编号。"""
    sequence = 0
    for item in exercises:
        if item.get("number") == "__preamble__":
            continue
        sequence += 1
        if item.get("number") != str(sequence):
            item["original_number"] = item.get("number")
            item["number"] = str(sequence)
            item["separator"] = "."


def _markdown_page_range(exercises: list[dict]) -> list[int]:
    """由 Markdown 的可选 ``<!-- page: N -->`` 标记推导 section 页范围。"""
    pages = [
        page
        for item in exercises
        for page in item.get("pages") or []
        if isinstance(page, int) and page >= 1
    ]
    return [min(pages), max(pages) + 1] if pages else []


def _is_markdown_answers_title(title: str) -> bool:
    """识别 Markdown 答案总标题，额外接受简短的“答案：”。"""
    if is_answers_title(title):
        return True
    normalized = re.sub(r"[\s:：]+", "", title).lower()
    return normalized in {
        "答案",
        "参考答案",
        "答案及解析",
        "答案与解析",
        "习题答案",
        "习题解答",
    }


def _split_markdown_answer_tail(markdown: str) -> tuple[str, str | None, str | None]:
    """从首个答案标题处分离正文与答案尾部，并保留标题供答案切块使用。"""
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not HEADING_RE.match(line):
            continue
        heading = re.sub(r"^\s*#+\s*", "", line).strip()
        if _is_markdown_answers_title(heading):
            return "\n".join(lines[:index]), heading, "\n".join(lines[index:])
    return markdown, None, None


def extract_exercise_from_markdown(
    markdown: str,
    *,
    doc_type: str = "textbook",
    document_name: str = "",
    title: str = "习题",
    path: str | None = None,
) -> Iterator[dict]:
    """从 Markdown 切分习题，流式返回固定公共 section JSON。

    ``markdown`` 可以包含多个 Markdown 标题；普通课本和习题集会按解析出的
    标题自动分成独立 section。若遇到“答案 / 参考答案 / 习题解答”等答案总标题，
    其后内容会作为 ``is_answers: true`` 的答案 section 单独返回。没有标题时，
    全部内容归入 ``title``。试卷模式
    则沿用 PDF 试卷流程，按“一、选择题 / 二、填空题 / 三、解答题”等题型标题
    返回独立 section。Markdown 内若包含 ``<!-- page: N -->``，会保留习题页码；
    纯 Markdown 没有 OCR 原始 JSON，因此每题 ``bbox`` 固定为空数组。

    此接口与 ``extract_exercise`` 一样返回迭代器，但不需要模型、OCR 服务或
    缓存目录。返回内容始终是可 JSON 序列化的公共 section schema。
    """
    if not isinstance(markdown, str):
        raise TypeError("markdown 必须是字符串")
    if doc_type not in ("textbook", "exercise_book", "exampaper"):
        raise ValueError("doc_type 必须是 textbook、exercise_book 或 exampaper")
    if not isinstance(document_name, str) or not isinstance(title, str):
        raise TypeError("document_name 和 title 必须是字符串")
    if path is not None and not isinstance(path, str):
        raise TypeError("path 必须是字符串或 None")

    default_title = title.strip() or "习题"
    base_path = path.strip() if path else ""

    if doc_type == "exampaper":
        root = {
            "title": default_title,
            "has_exercise": True,
            "exampaper": True,
            "children": [],
            "exercise_page_range": [],
            "tail_search_used": False,
            "exercise_content": markdown,
        }
        exampaper.extract_exercises([root])

        def exampaper_sections() -> Iterator[dict]:
            for node in root.get("children", []):
                node_path = "/".join(
                    part for part in (base_path or default_title, node["title"]) if part
                )
                public = public_section_from_node(
                    node, path=node_path, document_name=document_name
                )
                if public is not None:
                    yield public

        return exampaper_sections()

    # 先统一抽取图片/表格，再一次性切题。split_exercises 会把真正的 Markdown
    # 标题记录到 item.section，并能识别被 OCR 错标为标题的题号行；因此不能在
    # 这里简单按 '#' 行预切，避免把 "### 3. 题目" 误拆成一个 section。
    question_markdown, answer_title, answer_markdown = _split_markdown_answer_tail(markdown)
    source = (
        _normalize_exercise_prefixes(question_markdown)
        if doc_type == "exercise_book"
        else question_markdown
    )
    source, figures = extract_figures(source)
    parsed = split_exercises(
        source,
        check_section_end=False,
        split_solutions=False,
    )
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in parsed:
        if item.get("number") == "__preamble__":
            continue
        section_title = str(item.get("section") or default_title).strip() or default_title
        if section_title not in grouped:
            grouped[section_title] = []
            order.append(section_title)
        grouped[section_title].append(item)

    def markdown_sections() -> Iterator[dict]:
        for section_title in order:
            exercises = grouped[section_title]
            split_solutions_for_section(
                exercises, section_title, doc_type=doc_type
            )
            attach_figures(exercises, figures)
            convert_exercise_tables(exercises)
            for item in exercises:
                item["question_type"] = "short_answer"
                item.setdefault("answer", "")
                item.setdefault("solution_steps", "")
            _renumber_markdown_exercises(exercises)
            section_path = "/".join(
                part for part in (base_path, section_title) if part
            ) or section_title
            node = {
                "title": section_title,
                "has_exercise": True,
                "children": [],
                "exercise_page_range": _markdown_page_range(exercises),
                "tail_search_used": False,
                "exercises": exercises,
            }
            public = public_section_from_node(
                node, path=section_path, document_name=document_name
            )
            if public is not None:
                yield public

        if answer_title is not None and answer_markdown is not None:
            answer_path = "/".join(
                part for part in (base_path, answer_title) if part
            ) or answer_title
            yield public_answer_section_from_node(
                {
                    "title": answer_title,
                    "is_answers": True,
                    "exercise_page_range": [],
                    "answer_sections": answers.split_answer_sections(answer_markdown),
                },
                path=answer_path,
                document_name=document_name,
            )

    return markdown_sections()


def _resolve_model_dir(model_dir: Optional[str]) -> Optional[str]:
    """解析模型目录：显式参数优先，其次环境变量，最后 None（交给调用方判断）。

    toc_forge 需要显式模型目录，不内置任何机器路径；
    环境变量 EXERCISE_EXTRACTOR_MODEL_DIR 用于跨机器配置。
    """
    if model_dir:
        return model_dir
    return os.environ.get("EXERCISE_EXTRACTOR_MODEL_DIR")


def extract_exercise(
    source: str | list[str],
    doc_type: str = "textbook",
    output_dir: Optional[str] = None,
    ocr_cache: str = ".ocr_cache",
    model_dir: Optional[str] = None,
    vl_rec_backend: str = DEFAULT_VL_REC_BACKEND,
    vl_rec_server_url: str = DEFAULT_VL_SERVER_URL,
    vl_rec_max_concurrency: int = DEFAULT_VL_REC_MAX_CONCURRENCY,
    vl_rec_api_model_name: str = DEFAULT_VL_REC_API_MODEL_NAME,
    toc_vllm_name: str = "qwen3.7-flash",
    toc_api_base_url: Optional[str] = None,
    toc_api_key: Optional[str] = None,
    ocr_device: Optional[str] = None,
    ocr_engine: Optional[str] = None,
    oss_endpoint: Optional[str] = None,
    oss_access_key: Optional[str] = None,
    oss_secret_key: Optional[str] = None,
    oss_bucket: Optional[str] = None,
    oss_region: Optional[str] = None,
    oss_prefix: str = "",
    oss_use_ssl: bool = False,
    oss_public_base_url: Optional[str] = None,
    write_outputs: bool = False,
) -> Iterator[dict]:
    """提取 PDF 或一张/多张图片中的习题，返回公共 section 迭代器。

    source：PDF 路径，或一张/多张图片路径。输入类型按扩展名自动识别；图片
    按连续页面处理且不经过目录识别，三种 doc_type 均复用对应的习题切分逻辑。
    doc_type：textbook / exercise_book / exampaper。
    model_dir：模型目录；为 None 时读环境变量
    EXERCISE_EXTRACTOR_MODEL_DIR（toc_forge 需要显式模型目录）。
    vl_rec_*：PaddleOCR-VL 引擎初始化参数（backend / server_url /
    并发数 / 引擎模型名）。
    toc_vllm_name / toc_api_base_url / toc_api_key：目录识别 vLLM 配置；模型名
    默认使用 ``qwen3.7-flash``，地址和密钥未显式传入时分别读取
    ``TOC_FORGE_API_BASE_URL``/``ALIYUN_BASE_URL``/``OPENAI_BASE_URL`` 和
    ``TOC_FORGE_API_KEY``/``ALIYUN_API_KEY``/``OPENAI_API_KEY``。
    ocr_device / ocr_engine：PaddleOCR-VL 推理设备与引擎
    （如 device="gpu"/"cpu"，engine="paddle"/"onnxruntime"），
    None 时交给 paddlex 默认。
    ocr_device / ocr_engine 同时用于 PaddleOCR-VL 和 toc_forge 的 OCR。
    oss_*：OSS/MinIO 配置。四个必填项（endpoint/access_key/secret_key/
    bucket）齐全时启用图片上传：把 markdown 里的本地图片传到 OSS，
    src 替换为公开 URL（优先 oss_public_base_url，否则
    http(s)://endpoint/bucket/对象键）；缺任一项则不启用。
    write_outputs=True 时必须同时给 output_dir，会额外写出每个 section 的
    md/json 以及 report.json；不再写总的 exercises.json。

    返回迭代器：每完成一个有编号习题的 section 就 yield 一个不含
    ``children`` 的公共 JSON object。section 的 ``path`` 使用 ``/`` 记录
    其在目录树中的完整路径；答案节点和无编号习题节点不会 yield。
    参数校验（文件存在、输入类型、model_dir）在调用时立即执行。
    """
    source_paths = (
        [path.strip() for path in source.split(",")]
        if isinstance(source, str) and "," in source
        else ([source] if isinstance(source, str) else source)
    )
    if write_outputs and not output_dir:
        raise ValueError("write_outputs=True 时需要提供 output_dir")
    model_dir = _resolve_model_dir(model_dir)
    if not model_dir:
        raise ValueError(
            "需要提供 model_dir，或设置环境变量 EXERCISE_EXTRACTOR_MODEL_DIR"
        )
    prepared_source, source_type, cleanup_path = _prepare_source(source, ocr_cache)
    args = SimpleNamespace(
        input=prepared_source,
        source_type=source_type,
        document_name=os.path.splitext(os.path.basename(source_paths[0]))[0],
        output=output_dir,
        ocr_cache=ocr_cache,
        model_dir=model_dir,
        vl_rec_backend=vl_rec_backend,
        vl_rec_server_url=vl_rec_server_url,
        vl_rec_max_concurrency=vl_rec_max_concurrency,
        vl_rec_api_model_name=vl_rec_api_model_name,
        toc_vllm_name=toc_vllm_name,
        toc_api_base_url=toc_api_base_url,
        toc_api_key=toc_api_key,
        ocr_device=ocr_device,
        ocr_engine=ocr_engine,
        oss_endpoint=oss_endpoint,
        oss_access_key=oss_access_key,
        oss_secret_key=oss_secret_key,
        oss_bucket=oss_bucket,
        oss_region=oss_region,
        oss_prefix=oss_prefix,
        oss_use_ssl=oss_use_ssl,
        oss_public_base_url=oss_public_base_url,
        vl_model=vl_rec_api_model_name,
        doc_type=doc_type,
    )
    internal_sections = iter_sections(args, write_outputs_enabled=write_outputs)
    document_name = args.document_name

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

    result = public_sections()
    if not cleanup_path:
        return result

    def cleanup_after_iteration() -> Iterator[dict]:
        try:
            yield from result
        finally:
            try:
                os.unlink(cleanup_path)
            except FileNotFoundError:
                pass

    return cleanup_after_iteration()


def extract_exercise_from_image(source: str | list[str], **kwargs) -> Iterator[dict]:
    """从一张或多张图片提取习题；参数与 ``extract_exercise`` 相同。"""
    return extract_exercise(source, **kwargs)


# 兼容旧名（v0.1.0 早期接口），后续可移除
extract_pdf = extract_exercise
