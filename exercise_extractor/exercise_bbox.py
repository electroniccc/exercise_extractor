"""把结构化习题映射回 PaddleOCR-VL 解析块的页面坐标。

OCR 阶段会在 ``page_N/page.json`` 保存 PaddleOCR-VL 的原始 JSON。这里仅
使用 ``parsing_res_list`` 中已经合并好的 ``block_content/block_bbox``，不使用
更细碎的版面检测框；随后根据题号、题目文本和习题页码，为每道题附加题目级
``bbox``。缺少 JSON（例如旧缓存）或无法可靠匹配时返回空数组，不影响提取。
"""

from __future__ import annotations

from difflib import SequenceMatcher
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

MIN_MATCH_SCORE = 0.35


def _normalize_match_text(text: str) -> str:
    text = re.sub(r"<[^>]+>|[#*_`$\\{}]", "", text)
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()


def _normalize_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        numbers = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if len(numbers) == 4:
        x1, y1, x2, y2 = numbers
    elif len(numbers) % 2 == 0:
        xs, ys = numbers[0::2], numbers[1::2]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    else:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _result_payload(value: Any) -> dict[str, Any] | None:
    """兼容 PaddleX 直接 payload 及少数版本外包一层 ``res`` 的 JSON。"""
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("parsing_res_list"), list):
        return value
    for key in ("res", "result", "data"):
        child = value.get(key)
        if isinstance(child, dict) and isinstance(child.get("parsing_res_list"), list):
            return child
    return None


def load_page_blocks(markdown_path: str, page: int) -> list[dict[str, Any]]:
    """读取一页 OCR JSON，返回可用于题目匹配的解析块。"""
    json_path = os.path.join(markdown_path, f"page_{page}", "page.json")
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, encoding="utf-8") as file:
            payload = _result_payload(json.load(file))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "读取 OCR JSON 失败: page=%d path=%s error=%s", page, json_path, exc
        )
        return []
    if payload is None:
        logger.warning("OCR JSON 缺少 parsing_res_list: page=%d path=%s", page, json_path)
        return []

    blocks: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[float, ...]]] = set()
    for index, item in enumerate(payload["parsing_res_list"]):
        if not isinstance(item, dict):
            continue
        text = item.get("block_content")
        bbox = _normalize_bbox(item.get("block_bbox"))
        if not isinstance(text, str) or not text.strip() or bbox is None:
            continue
        key = (text, tuple(bbox))
        if key in seen:
            continue
        seen.add(key)
        blocks.append(
            {
                "page": page,
                "index": index,
                "text": text,
                "normalized": _normalize_match_text(text),
                "bbox": bbox,
            }
        )
    return blocks


def _candidate_pages(exercise: dict[str, Any], node: dict[str, Any]) -> list[int]:
    pages = exercise.get("pages")
    if isinstance(pages, list) and pages:
        try:
            start, end = int(pages[0]), int(pages[-1])
        except (TypeError, ValueError):
            return []
        return list(range(min(start, end), max(start, end) + 1))
    page_range = node.get("exercise_page_range")
    if isinstance(page_range, list) and len(page_range) >= 2:
        try:
            start, end = int(page_range[0]), int(page_range[1])
        except (TypeError, ValueError):
            return []
        return list(range(start, max(start, end)))
    return []


def _number_matches(number: str, block_text: str) -> bool:
    marker = re.match(
        r"\s*(?:#{1,6}\s*)?[*^$\s]*[（(]?(\d{1,3}(?:[.．]\d{1,3})?)[.)、．）]",
        block_text,
    )
    if not marker:
        return False
    normalized_number = str(number).strip().replace("．", ".")
    return marker.group(1).replace("．", ".") == normalized_number


def _match_score(exercise: dict[str, Any], block: dict[str, Any]) -> float:
    target = _normalize_match_text(str(exercise.get("content") or ""))
    candidate = block["normalized"]
    if not target or not candidate:
        return 0.0
    target, candidate = target[:1600], candidate[:1600]
    matcher = SequenceMatcher(None, target, candidate)
    score = matcher.ratio()
    longest = matcher.find_longest_match(0, len(target), 0, len(candidate)).size
    # 以题干覆盖率为准，避免一个只有一两个字的正文块因为完全包含于题干而
    # 得到高分；candidate 较长（包含选项或解答）时仍允许完整覆盖题干。
    score = max(score, longest / max(1, len(target)))
    if target in candidate:
        score = max(score, len(target) / len(candidate))
    number = str(exercise.get("original_number") or exercise.get("number") or "")
    if _number_matches(number, block["text"]):
        score += 0.5
    return score


def attach_exercise_bboxes(
    nodes: list[dict[str, Any]], markdown_path: str
) -> None:
    """原地为目录树/节点列表中的每道习题写入固定格式的 ``bbox``。

    字段格式为 ``[{"page": N, "bbox": [x1, y1, x2, y2]}]``。当前
    PaddleOCR-VL 通常会把一道题合并为一个 parsing block，因此每道题一般只有
    一项；数组形式为跨页题目和后续多块合并保留扩展空间。
    """
    page_cache: dict[int, list[dict[str, Any]]] = {}

    def blocks_for(page: int) -> list[dict[str, Any]]:
        if page not in page_cache:
            page_cache[page] = load_page_blocks(markdown_path, page)
        return page_cache[page]

    def walk(items: list[dict[str, Any]]) -> None:
        for node in items:
            used: set[tuple[int, int]] = set()
            for exercise in node.get("exercises", []):
                exercise["bbox"] = []
                best: dict[str, Any] | None = None
                best_score = 0.0
                for page in _candidate_pages(exercise, node):
                    for block in blocks_for(page):
                        if (page, block["index"]) in used:
                            continue
                        score = _match_score(exercise, block)
                        if score > best_score:
                            best, best_score = block, score
                if best is None or best_score < MIN_MATCH_SCORE:
                    continue
                used.add((best["page"], best["index"]))
                exercise["bbox"] = [
                    {"page": best["page"], "bbox": list(best["bbox"])}
                ]
            walk(node.get("children", []))

    walk(nodes)
