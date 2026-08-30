#!/usr/bin/env python3
"""PaddleOCR-VL Markdown/JSON/bbox 验证脚本。

示例：
    .venv/bin/python poc/ocr_bbox.py poc/sample.png --output-dir poc/ocr_bbox_data

脚本会保存原始 ``result.md``、``result.json``、提取出的题目 bbox 映射，
以及带 bbox 标注的 ``annotated.png``。不同 PaddleOCR/PaddleX 版本的 JSON
字段略有差异，因此这里采用递归方式兼容常见的 ``bbox/box/coordinate`` 字段。
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import re
from pathlib import Path
import sys
from typing import Any

from PIL import Image, ImageDraw
from paddleocr import PaddleOCRVL

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exercise_extractor.parse_md_exercises import split_exercises
from exercise_extractor.exampaper import split_type_sections, _split_section_exercises


def _jsonable_result(result: Any) -> Any:
    payload = getattr(result, "json", None)
    if callable(payload):
        payload = payload()
    if payload is None:
        payload = getattr(result, "_to_json", lambda: None)()
    if payload is None:
        raise RuntimeError("OCR result 不提供 json 属性")
    return payload


def _bbox(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        for key in ("bbox", "box", "coordinate", "poly"):
            raw = value.get(key)
            if isinstance(raw, (list, tuple)) and len(raw) >= 4:
                try:
                    nums = [float(x) for x in raw]
                    if len(nums) == 4:
                        return nums
                    xs, ys = nums[0::2], nums[1::2]
                    return [min(xs), min(ys), max(xs), max(ys)]
                except (TypeError, ValueError):
                    pass
    return None


def _text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("text", "content", "rec_text", "label"):
        if isinstance(value.get(key), str):
            return value[key]
    return ""


def _collect_blocks(value: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        # PaddleOCR-VL 文档解析 JSON 的题目文本在 parsing_res_list 中，
        # 字段名是 block_content/block_bbox；layout_det_res 只是版面小块，
        # 不能直接拿来当题目框。
        box = value.get("block_bbox")
        text = value.get("block_content")
        if isinstance(box, (list, tuple)) and isinstance(text, str):
            try:
                box = [float(x) for x in box]
            except (TypeError, ValueError):
                box = None
        if box is not None and text.strip():
            out.append({"text": text, "bbox": box})
        for child in value.values():
            _collect_blocks(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_blocks(child, out)


def _normalize_match_text(text: str) -> str:
    text = re.sub(r"<[^>]+>|[#*_`$\\{}]", "", text)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()


def _assign_exercises(markdown: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """用主模块切分 Markdown，再将每道题匹配到 OCR parsing block。"""
    type_sections, _ = split_type_sections(markdown)
    if type_sections:
        exercises = [
            item
            for section in type_sections
            for item in _split_section_exercises(section)
        ]
    else:
        exercises = [
            item
            for item in split_exercises(
                markdown, check_section_end=False, split_solutions=False
            )
            if item.get("number") != "__preamble__"
        ]
    available = set(range(len(blocks)))
    result: list[dict[str, Any]] = []
    for index, exercise in enumerate(exercises):
        number = str(exercise.get("number") or "")
        target = _normalize_match_text(exercise.get("content") or "")
        best_index: int | None = None
        best_score = 0.0
        for block_index in available:
            block_text = blocks[block_index]["text"]
            candidate = _normalize_match_text(block_text)
            if not target or not candidate:
                continue
            score = SequenceMatcher(None, target[:800], candidate[:800]).ratio()
            marker = re.match(r"\s*[（(]?(\d{1,3})[.)、）]", block_text)
            if marker and marker.group(1) == number:
                score += 0.5
            if score > best_score:
                best_score, best_index = score, block_index
        matched = blocks[best_index] if best_index is not None and best_score >= 0.35 else None
        if best_index is not None and matched is not None:
            available.remove(best_index)
        result.append(
            {
                "index": index,
                "number": number,
                "content": exercise.get("content") or "",
                "bbox": matched["bbox"] if matched else None,
                "match_score": round(min(best_score, 1.0), 4),
                "matched_block_content": matched["text"] if matched else None,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="保存 PaddleOCR-VL JSON 并标注 OCR bbox")
    parser.add_argument("input", type=Path, help="待 OCR 的图片文件")
    parser.add_argument("--output-dir", type=Path, default=Path("poc/ocr_bbox_data"))
    parser.add_argument("--server-url", default="http://localhost:8080/v1")
    parser.add_argument("--model", default="PaddleOCR-VL-1.6-0.9B")
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"输入图片不存在：{args.input}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        vl_rec_backend="vllm-server",
        vl_rec_server_url=args.server_url,
        vl_rec_api_model_name=args.model,
    )
    result = next(pipeline.predict_iter([str(args.input)]))
    result.save_to_markdown(save_path=str(args.output_dir / "result.md"), show_formula_number=True)
    result.save_to_json(save_path=str(args.output_dir / "result.json"), ensure_ascii=False)
    payload = _jsonable_result(result)
    (args.output_dir / "result_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = (args.output_dir / "result.md").read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    _collect_blocks(payload, blocks)
    blocks = list({(b["text"], tuple(b["bbox"])): b for b in blocks}.values())
    mapping = _assign_exercises(markdown, blocks)
    (args.output_dir / "exercise_bboxes.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    image = Image.open(args.input).convert("RGB")
    draw = ImageDraw.Draw(image)
    for item in mapping:
        number, box = item["number"], item["bbox"]
        if not box:
            continue
        x1, y1, x2, y2 = box
        draw.rectangle((x1, y1, x2, y2), outline="red", width=4)
        draw.text((x1 + 4, max(0, y1 - 22)), number, fill="red")
    image.save(args.output_dir / "annotated.png")
    print(f"Markdown: {args.output_dir / 'result.md'}")
    print(f"JSON: {args.output_dir / 'result.json'}")
    print(f"题目 bbox: {args.output_dir / 'exercise_bboxes.json'}")
    print(f"标注图: {args.output_dir / 'annotated.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
