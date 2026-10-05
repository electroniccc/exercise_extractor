"""目录识别与 OCR：把输入 PDF 变成各习题区域的 OCR markdown 缓存。

- build_sections：跑 toc_forge 目录识别（阿里云 vLLM 视觉 API）得到目录树，
  经 build_section_tree 筛出习题区域；
- create_ocr_pipeline / ocr_pages：用 PaddleOCR-VL（vllm-server）对
  未缓存的习题页做 OCR，结果按 PDF 哈希缓存并以 READY 标记原子发布。
"""

import argparse
import json
import logging
import os
import re

import pymupdf
import toc_forge
from paddleocr import PaddleOCRVL

from . import exampaper, exercise_book
from .llm_usage import LlmUsage, UsageCallback, report_usage
from .extraction import SectionNode, build_section_tree, collect_pages_to_ocr
from .ocr_cache import (
    clear_page_failure,
    create_page_staging_dir,
    discard_page_staging,
    ocr_cache_lock,
    page_cache_is_ready,
    publish_page_cache,
    record_page_failure,
)
from .utils import fix_markdown_block_latex, image_from_page

logger = logging.getLogger(__name__)

DEFAULT_TOC_VLLM_NAME = "qwen3.7-flash"
OCR_PAGE_BATCH_SIZE = 4


def _toc_ocr_kwargs(args: argparse.Namespace) -> dict[str, str]:
    """返回 toc_forge 的 OCR 参数，并让 --ocr-* 与其保持一致。"""
    ocr_device = getattr(args, "ocr_device", None)
    ocr_engine = getattr(args, "ocr_engine", None)
    result: dict[str, str] = {}
    if ocr_device:
        result["device"] = ocr_device
    if ocr_engine:
        result["engine"] = ocr_engine
    return result


def _toc_llm_config(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
    """读取目录识别 vLLM 配置；密钥和地址默认来自环境变量。"""
    model = getattr(args, "toc_vllm_name", None) or DEFAULT_TOC_VLLM_NAME
    api_base_url = getattr(args, "toc_api_base_url", None) or os.environ.get(
        "TOC_FORGE_API_BASE_URL"
    ) or os.environ.get("ALIYUN_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    api_key = getattr(args, "toc_api_key", None) or os.environ.get(
        "TOC_FORGE_API_KEY"
    ) or os.environ.get("ALIYUN_API_KEY") or os.environ.get("OPENAI_API_KEY")
    return model, api_base_url, api_key


def build_sections(
    args: argparse.Namespace,
    *,
    usage_callback: UsageCallback | None = None,
) -> list[SectionNode]:
    """跑 toc_forge 目录识别，返回习题区域小节树。

    toc_forge 内部自带缓存（目录页 layout 检测、toc 树 vllm 结果），
    重复跑只会命中缓存，不会重复调用 OCR/API。文件哈希与目录识别无关，
    由调用方（main.py）自行计算。

    exampaper 不走目录识别：整卷全量 OCR，直接返回整卷节点。
    """
    source_type = getattr(args, "source_type", None)
    if source_type is None:
        suffix = os.path.splitext(args.input)[1].lower()
        source_type = (
            "image"
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
            else "pdf"
        )
    if source_type == "image":
        # PyMuPDF 可以直接把常见图片打开为单页文档；图片没有目录可供
        # toc_forge 识别，因此三种模式都从一个整页的合成节点开始，后续
        # 仍复用同一套 OCR、题号切分和题型处理逻辑。
        doc = pymupdf.open(args.input)
        doc_page_count = doc.page_count
        doc.close()
        title = getattr(
            args, "document_name", os.path.splitext(os.path.basename(args.input))[0]
        )
        if args.doc_type == "exampaper":
            return exampaper.build_exampaper_sections(title, doc_page_count)
        return [{
            "title": title,
            "has_exercise": True,
            "exercise_page_range": [1, doc_page_count + 1],
            "children": [],
        }]
    if args.doc_type == "exampaper":
        doc = pymupdf.open(args.input)
        doc_page_count = doc.page_count
        doc.close()
        title = os.path.splitext(os.path.basename(args.input))[0]
        return exampaper.build_exampaper_sections(title, doc_page_count)
    # toc_forge 的目录页 OCR 与习题页 OCR 应使用同一套 Paddle 推理设备/引擎。
    toc_engine_kwargs = _toc_ocr_kwargs(args)
    # 目录识别改用 OpenAI-compatible vLLM API（视觉模型直接读目录页图片）。
    toc_vllm_name, toc_api_base_url, toc_api_key = _toc_llm_config(args)
    if not toc_api_key:
        logger.warning("未配置 toc_forge API key，目录识别 API 调用可能鉴权失败")
    if not toc_api_base_url:
        logger.warning("未配置 toc_forge API base URL，目录识别 API 调用可能失败")
    bookmark_result = toc_forge.bookmark_pdf(
        args.input,
        "./temp",
        model_dir=args.model_dir,
        cache_dir=os.path.join(args.ocr_cache, "toc_forge"),
        toc_strategy="vllm",
        api_base_url=toc_api_base_url,
        api_key=toc_api_key,
        vllm_name=toc_vllm_name,
        **toc_engine_kwargs,
    )
    # toc_forge 0.3+ 已聚合本次目录识别所有实际 LLM 请求的 usage。目录
    # 缓存命中时数字为零，仍报告该次操作，供宿主统一留痕。
    report_usage(
        usage_callback,
        LlmUsage(
            operation="toc_forge.vllm",
            model=toc_vllm_name,
            input_tokens=getattr(bookmark_result, "input_tokens", None),
            output_tokens=getattr(bookmark_result, "output_tokens", None),
        ),
    )
    # toc_forge 0.3+ 返回 BookmarkResult；这里直接取结构化目录树。
    toc_tree = bookmark_result.toc_tree
    # 文档总页数：习题答案节点没有 boundary 时用它收尾
    doc = pymupdf.open(args.input)
    doc_page_count = doc.page_count
    doc.close()
    if args.doc_type == "exercise_book":
        return exercise_book.build_exercise_book_sections(toc_tree, doc_page_count)
    return build_section_tree(
        toc_tree, doc_type=args.doc_type, doc_page_count=doc_page_count
    )


def create_ocr_pipeline(args: argparse.Namespace) -> PaddleOCRVL:
    """初始化 VL OCR pipeline。

    构造只做配置校验，不连接服务；真正的推理在 ocr_pages 里。
    PaddleOCR-VL 的 genai 客户端默认把批大小设为 8192（整批页面合成一批），
    结果收集要等整批全部完成才输出；这里改为 8，ocr_pages 再按
    OCR_PAGE_BATCH_SIZE 分批送入，限制长卷的内存峰值。引擎参数由调用方
    通过 args 传入，CLI 与对外 API 共用同一套配置。
    """
    ocr_engine_kwargs = {}
    if getattr(args, "ocr_device", None):
        ocr_engine_kwargs["device"] = args.ocr_device
    if getattr(args, "ocr_engine", None):
        ocr_engine_kwargs["engine"] = args.ocr_engine
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        vl_rec_backend=getattr(args, "vl_rec_backend", "vllm-server"),
        vl_rec_server_url=getattr(
            args, "vl_rec_server_url", getattr(args, "vl_server_url", None)
        ),
        vl_rec_max_concurrency=getattr(args, "vl_rec_max_concurrency", 4),
        vl_rec_api_model_name=getattr(
            args, "vl_rec_api_model_name", "PaddleOCR-VL-1.6-0.9B"
        ),
        **ocr_engine_kwargs,
    )
    try:
        pipeline.paddlex_pipeline.vl_rec_model.batch_sampler.batch_size = 8
    except Exception as e:
        logger.warning("设置 VL 识别批大小失败（保持默认 8192）: %s", e)
    return pipeline


def _save_ocr_result(
    res, page_num: int, markdown_path: str, vl_model: str
) -> None:
    """把单页结果写到临时目录，校验 Markdown/JSON 后原子发布。"""
    if page_cache_is_ready(markdown_path, page_num):
        return
    staging_dir = create_page_staging_dir(markdown_path, page_num)
    try:
        res.save_to_markdown(
            save_path=os.path.join(staging_dir, "page.md"),
            show_formula_number=True,
        )
        # parsing_res_list 同时保留 block_content/block_bbox；Markdown 与 JSON
        # 必须作为同一页缓存一起成功，不能暴露其中任意一个半成品。
        res.save_to_json(
            save_path=os.path.join(staging_dir, "page.json"), ensure_ascii=False
        )
        # paddleocrvl 小 bug：$$ 块公式首尾带空格，VSCode preview 渲染不了。
        # 后处理也在临时目录完成，最终目录始终只出现完整版本。
        if "PaddleOCR" in vl_model:
            fix_markdown_block_latex(staging_dir)
        publish_page_cache(staging_dir, markdown_path, page_num)
        clear_page_failure(markdown_path, page_num)
    finally:
        discard_page_staging(staging_dir)


def _page_declared_blank(page) -> bool:
    """只识别 PDF 明确标出的空白页，避免误跳过扫描题页。"""
    lines = [line.strip() for line in page.get_text("text").splitlines()]
    lines = [line for line in lines if line]
    if any(line.lower() == "there are no questions printed on this page"
           for line in lines):
        return True
    if "BLANK PAGE" not in lines:
        return False
    return all(
        line == "BLANK PAGE"
        or re.fullmatch(r"\d{1,3}|\d{4}/\d{1,2}/[A-Z]/[A-Z]/\d{2}|©\s*UCLES\s*\d{4}", line)
        for line in lines
    )


def _cache_declared_blank_page(markdown_path: str, page_num: int) -> None:
    """为明确空白页发布可复用缓存，不向视觉模型发送无题内容。"""
    staging_dir = create_page_staging_dir(markdown_path, page_num)
    try:
        with open(os.path.join(staging_dir, "page.md"), "w", encoding="utf-8") as stream:
            stream.write("BLANK PAGE\n")
        with open(os.path.join(staging_dir, "page.json"), "w", encoding="utf-8") as stream:
            json.dump({"parsing_res_list": []}, stream)
        publish_page_cache(staging_dir, markdown_path, page_num)
        clear_page_failure(markdown_path, page_num)
    finally:
        discard_page_staging(staging_dir)


def _ocr_single_page(
    doc, page_num: int, markdown_path: str, vl_model: str, pipeline: PaddleOCRVL
) -> None:
    """单页 OCR；失败时记录 FAILED 标记并跳过，不让整本中断。"""
    if page_cache_is_ready(markdown_path, page_num):
        return
    try:
        img = image_from_page(doc[page_num - 1])
        res = next(pipeline.predict_iter([img]))
        _save_ocr_result(res, page_num, markdown_path, vl_model)
    except Exception as e:
        logger.error("第 %d 页 OCR 失败，跳过: %s", page_num, e)
        record_page_failure(markdown_path, page_num, e)


def ocr_pages(
    args: argparse.Namespace,
    sections: list[SectionNode],
    markdown_path: str,
    pipeline: PaddleOCRVL,
) -> None:
    """在 PDF hash 文件锁内补齐 OCR 页面，避免相同文档并发写缓存。"""
    with ocr_cache_lock(markdown_path):
        _ocr_pages_locked(args, sections, markdown_path, pipeline)


def _ocr_pages_locked(
    args: argparse.Namespace,
    sections: list[SectionNode],
    markdown_path: str,
    pipeline: PaddleOCRVL,
) -> None:
    """锁内重新检查缺页并生产缓存；调用方不得绕过 ``ocr_pages``。"""
    # 缺页必须在取得锁之后计算：等待锁期间，另一个请求可能已经完成 OCR。
    pages_to_ocr = collect_pages_to_ocr(sections, markdown_path)
    if not pages_to_ocr:
        logger.info("所有习题页均已有缓存，自动跳过 OCR")
        return
    if not args.input:
        raise SystemExit(
            f"还有 {len(pages_to_ocr)} 页未缓存（如第 {pages_to_ocr[0]} 页），"
            "补 OCR 需要 --input"
        )
    logger.info("待 OCR 页面 %d 个: %s", len(pages_to_ocr), pages_to_ocr[:30])
    doc = pymupdf.open(args.input)
    try:
        blank_pages = [
            page_num for page_num in pages_to_ocr
            if _page_declared_blank(doc[page_num - 1])
        ]
        for page_num in blank_pages:
            _cache_declared_blank_page(markdown_path, page_num)
        if blank_pages:
            logger.info("跳过 PDF 明确标注的空白页: %s", blank_pages)
            pages_to_ocr = [
                page_num for page_num in pages_to_ocr
                if page_num not in blank_pages
            ]
        if not pages_to_ocr:
            return
        for start in range(0, len(pages_to_ocr), OCR_PAGE_BATCH_SIZE):
            batch_pages = pages_to_ocr[start:start + OCR_PAGE_BATCH_SIZE]
            try:
                imgs = [image_from_page(doc[page_num - 1]) for page_num in batch_pages]
                for idx, res in enumerate(pipeline.predict_iter(imgs)):
                    if idx >= len(batch_pages):
                        break
                    _save_ocr_result(
                        res, batch_pages[idx], markdown_path, args.vl_model
                    )
                # 服务端可能正常结束却少返回页面；只补本批的缺页。
                missing = [
                    p for p in batch_pages
                    if not page_cache_is_ready(markdown_path, p)
                ]
                if missing:
                    logger.warning(
                        "批量 OCR 后仍有 %d 页未产出结果，逐页补跑: %s",
                        len(missing), missing,
                    )
                    fallback_pipeline = create_ocr_pipeline(args)
                    for page_num in missing:
                        _ocr_single_page(
                            doc, page_num, markdown_path, args.vl_model,
                            fallback_pipeline,
                        )
            except Exception as e:
                logger.warning("批量 OCR 失败，重建 pipeline 逐页处理并跳过失败页: %s", e)
                # 批量异常后 paddlex 的异步状态可能已损坏，重建 pipeline。
                fallback_pipeline = create_ocr_pipeline(args)
                pipeline = fallback_pipeline
                for page_num in batch_pages:
                    _ocr_single_page(
                        doc, page_num, markdown_path, args.vl_model,
                        fallback_pipeline,
                    )
    finally:
        doc.close()
