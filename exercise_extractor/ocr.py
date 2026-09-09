"""目录识别与 OCR：把输入 PDF 变成各习题区域的 OCR markdown 缓存。

- build_sections：跑 toc_forge 目录识别（阿里云 vLLM 视觉 API）得到目录树，
  经 build_section_tree 筛出习题区域；
- create_ocr_pipeline / ocr_pages：用 PaddleOCR-VL（vllm-server）对
  未缓存的习题页做 OCR，结果按 PDF 哈希缓存（page_N/page.md + page.json）。
"""

import argparse
import logging
import os

import pymupdf
import toc_forge
from paddleocr import PaddleOCRVL

from . import exampaper, exercise_book
from .extraction import SectionNode, build_section_tree, collect_pages_to_ocr
from .utils import fix_markdown_block_latex, image_from_page

logger = logging.getLogger(__name__)

DEFAULT_TOC_VLLM_NAME = "qwen3.7-flash"


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


def build_sections(args: argparse.Namespace) -> list[SectionNode]:
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
    结果收集要等整批全部完成才输出；这里改为 8，让 predict_iter 按
    8 个一批实时产出。引擎参数（backend/server_url/并发数/模型名）由调用方
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
    """保存单页 OCR 结果（已缓存则跳过）。"""
    page_dir = os.path.join(markdown_path, f"page_{page_num}")
    save_path = os.path.join(page_dir, "page.md")
    if os.path.exists(save_path):
        return
    res.save_to_markdown(save_path=save_path, show_formula_number=True)
    # parsing_res_list 同时保留了 block_content/block_bbox，后处理可据此把
    # 结构化习题映射回 PDF 页面。JSON 保存失败不应让已经成功的 OCR 文本作废。
    try:
        res.save_to_json(
            save_path=os.path.join(page_dir, "page.json"), ensure_ascii=False
        )
    except Exception as exc:
        logger.warning("第 %d 页 OCR JSON 保存失败，bbox 将为空: %s", page_num, exc)
    # 重跑成功后清掉上次失败的标记，让 FAILED.txt 只代表当前状态
    failed_path = os.path.join(page_dir, "FAILED.txt")
    if os.path.exists(failed_path):
        os.remove(failed_path)
    # paddleocrvl 小 bug：$$ 块公式首尾带空格，VSCode preview 渲染不了
    if "PaddleOCR" in vl_model:
        fix_markdown_block_latex(page_dir)


def _ocr_single_page(
    doc, page_num: int, markdown_path: str, vl_model: str, pipeline: PaddleOCRVL
) -> None:
    """单页 OCR；失败时记录 FAILED 标记并跳过，不让整本中断。"""
    page_dir = os.path.join(markdown_path, f"page_{page_num}")
    save_path = os.path.join(page_dir, "page.md")
    if os.path.exists(save_path):
        return
    try:
        img = image_from_page(doc[page_num - 1])
        res = next(pipeline.predict_iter([img]))
        _save_ocr_result(res, page_num, markdown_path, vl_model)
    except Exception as e:
        logger.error("第 %d 页 OCR 失败，跳过: %s", page_num, e)
        os.makedirs(page_dir, exist_ok=True)
        with open(os.path.join(page_dir, "FAILED.txt"), "w", encoding="utf-8") as f:
            f.write(str(e))


def ocr_pages(
    args: argparse.Namespace,
    sections: list[SectionNode],
    markdown_path: str,
    pipeline: PaddleOCRVL,
) -> None:
    """对习题页做 OCR；已缓存的页面自动跳过（全部有缓存则完全不碰 OCR 服务）。"""
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
    print(f"待 OCR 页面 {len(pages_to_ocr)} 个，请稍候…")
    doc = pymupdf.open(args.input)
    try:
        imgs = [image_from_page(doc[page_num - 1]) for page_num in pages_to_ocr]
        results = pipeline.predict_iter(imgs)
        for idx, res in enumerate(results):
            if idx >= len(pages_to_ocr):
                break
            _save_ocr_result(
                res, pages_to_ocr[idx], markdown_path, args.vl_model
            )
        # 防御：服务端可能比请求数少返回几个结果（静默截断，不抛异常），
        # 这里逐个补跑，避免整批正常结束时仍有页面静默缺失
        missing = [
            p
            for p in pages_to_ocr
            if not os.path.exists(
                os.path.join(markdown_path, f"page_{p}", "page.md")
            )
        ]
        if missing:
            logger.warning(
                "批量 OCR 后仍有 %d 页未产出结果，逐页补跑: %s",
                len(missing),
                missing,
            )
            fallback_pipeline = create_ocr_pipeline(args)
            for page_num in missing:
                _ocr_single_page(
                    doc, page_num, markdown_path, args.vl_model, fallback_pipeline
                )
    except Exception as e:
        logger.warning("批量 OCR 失败，重建 pipeline 逐页处理并跳过失败页: %s", e)
        print(f"批量 OCR 失败（{e}），改为逐页处理并跳过失败页…")
        # 批量异常后 paddlex 的异步状态可能已损坏，重建 pipeline 更稳妥
        fallback_pipeline = create_ocr_pipeline(args)
        for page_num in pages_to_ocr:
            _ocr_single_page(
                doc, page_num, markdown_path, args.vl_model, fallback_pipeline
            )
    finally:
        doc.close()
