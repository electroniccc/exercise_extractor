"""Restore column order in cached textbook exercise pages using OCR boxes."""

import json
import re
from pathlib import Path

from .parse_md_exercises import _section_heading_matches
from .textbook_regions import _heading_pattern


_QUESTION = re.compile(r"^\s*\d{1,3}[.．、)](?:\s|$)")
_BARE_QUESTION = re.compile(r"^\s*\d{1,3}[.．、)]\s*$")


def ordered_exercise_page(
    markdown_path: str, page_num: int, node: dict, original: str,
) -> str:
    """Return corrected Markdown only when both exercise columns are clear."""
    heading_number = node.get("_exercise_heading_number")
    if not node.get("implicit") or not heading_number:
        return original
    page_dir = Path(markdown_path) / f"page_{page_num}"
    try:
        payload = json.loads((page_dir / "page.json").read_text(encoding="utf-8"))
        blocks = payload["parsing_res_list"]
        width = float(payload["width"])
    except (OSError, ValueError, KeyError, TypeError):
        return original
    if not isinstance(blocks, list) or not width:
        return original
    if any(not isinstance(block, dict)
           or not isinstance(block.get("block_bbox"), list)
           or len(block["block_bbox"]) < 4 for block in blocks):
        return original

    heading = _heading_pattern(heading_number)
    start = next((index + 1 for index, block in enumerate(blocks)
                  if block.get("block_label") in ("paragraph_title", "text")
                  and heading.search(str(block.get("block_content", "")))), None)
    if page_num == node["exercise_page_range"][0] and start is None:
        return original
    start = start or 0
    end = len(blocks)
    boundary_title = node.get("boundary_title")
    if boundary_title:
        for index in range(start, len(blocks)):
            block = blocks[index]
            if (block.get("block_label") in ("paragraph_title", "doc_title")
                    and _section_heading_matches(block.get("block_content"), boundary_title)):
                end = index
                break
    region = blocks[start:end]
    midpoint = width / 2

    def side(block):
        bbox = block.get("block_bbox")
        if not isinstance(bbox, list) or len(bbox) < 4:
            return None
        return 0 if (bbox[0] + bbox[2]) / 2 < midpoint else 1

    counts = [0, 0]
    for block in region:
        if (block.get("block_label") in ("text", "number")
                and _QUESTION.match(str(block.get("block_content", "")))):
            block_side = side(block)
            if block_side is not None:
                counts[block_side] += 1
    if min(counts) < 2:
        return original

    numbered = []
    for block in region:
        content = str(block.get("block_content", ""))
        if _QUESTION.match(content):
            number = re.match(r"\s*(\d{1,3})", content)
            if number:
                numbered.append((int(number[1]), side(block), block))
    left_numbers = [number for number, block_side, _ in numbered if block_side == 0]
    right_numbers = [number for number, block_side, _ in numbered if block_side == 1]
    if left_numbers and right_numbers:
        right_x = min(block["block_bbox"][0] for _, block_side, block in numbered
                      if block_side == 1)
        for block in list(region):
            content = str(block.get("block_content", ""))
            lines = content.splitlines()
            if side(block) != 0 or len(lines) < 2:
                continue
            trailing = _BARE_QUESTION.match(lines[-1])
            if not trailing:
                continue
            number = int(re.match(r"\s*(\d{1,3})", trailing[0])[1])
            if not (number > max(left_numbers)
                    and min(right_numbers) - 1 <= number <= max(right_numbers) + 1):
                continue
            block["block_content"] = "\n".join(lines[:-1]).rstrip()
            moved = dict(block)
            moved["block_content"] = lines[-1].strip()
            top = block["block_bbox"][1]
            moved["block_bbox"] = [right_x, top, right_x + 20, top + 20]
            region.append(moved)

    def order_rows(items):
        rows = []
        for block in sorted(items, key=lambda item: item["block_bbox"][1]):
            top = block["block_bbox"][1]
            if not rows or top - rows[-1][0] > 12:
                rows.append((top, [block]))
            else:
                rows[-1][1].append(block)
        return [block for _, row in rows
                for block in sorted(row, key=lambda item: item["block_bbox"][0])]

    left = order_rows([block for block in region if side(block) != 1])
    right = order_rows([block for block in region if side(block) == 1])
    ordered = blocks[:start] + left + right + blocks[end:]
    # Paddle marks some exercise numbers as page-number blocks and omits them
    # from Markdown. Only restore punctuated numbers inside the exercise area.
    for block in left + right:
        if (block.get("block_label") == "number"
                and _BARE_QUESTION.match(str(block.get("block_content", "")))):
            block["block_label"] = "text"

    # Reuse the installed Paddle formatter so formulas, tables, and cropped
    # images retain the same Markdown shape as the original OCR page.
    from paddlex.inference.pipelines.paddleocr_vl.result import (
        PaddleOCRVLBlock, PaddleOCRVLResult,
    )

    rendered_blocks = []
    for item in ordered:
        bbox = item.get("block_bbox")
        if not isinstance(bbox, list) or len(bbox) < 4:
            return original
        block = PaddleOCRVLBlock(
            item.get("block_label", "text"), bbox,
            str(item.get("block_content", "")),
        )
        if block.label in ("image", "chart", "seal"):
            stem = "img_in_image_box_" + "_".join(map(str, bbox))
            image = next((path for path in (page_dir / "imgs").glob(stem + ".*")), None)
            if image:
                block.image = {"path": f"imgs/{image.name}", "img": None}
        rendered_blocks.append(block)
    payload["parsing_res_list"] = rendered_blocks
    payload["imgs_in_doc"] = []
    rendered = PaddleOCRVLResult(payload)._to_markdown(
        show_formula_number=True,
    )["markdown_texts"]
    original_images = set(re.findall(r'<img\s+src="([^"]+)"', original))
    rendered_images = set(re.findall(r'<img\s+src="([^"]+)"', rendered))
    if original_images - rendered_images:
        return original
    return re.sub(r"\s(\$\$.+?\$\$)\s\n", lambda match: match[1] + "\n", rendered)
