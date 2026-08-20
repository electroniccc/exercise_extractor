#!/usr/bin/env python3
"""快速调试一个公共 section JSON 的基础 LLM 审校。

示例：
    python poc/review_basic.py \
        output/tongji_gaoshu_first1/41_习题_1–2.json \
        --output /tmp/review_1_2.json \
        --diff

输入和输出都是单个 section 的 JSON object；diff 不打印到终端，而写到
输出文件同目录下的 ``.diff.txt`` 文件。
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exercise_extractor.review import (  # noqa: E402
    DEFAULT_REVIEW_BASE_URL,
    DEFAULT_REVIEW_MODEL,
    review_basic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="调试单个 JSON section 的基础审校")
    parser.add_argument("input", type=Path, help="待审校的 section JSON 文件")
    parser.add_argument("--document-name", default=None, help="覆盖 section.document_name")
    parser.add_argument("--section-name", default=None, help="覆盖 section.path")
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"OpenAI-compatible base URL（默认 {DEFAULT_REVIEW_BASE_URL}）",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key（默认读取 OPENAI_API_KEY 或 ALIYUN_API_KEY）",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_REVIEW_MODEL,
        help=f"审校模型（默认 {DEFAULT_REVIEW_MODEL}）",
    )
    parser.add_argument("--output", type=Path, required=True, help="审校后的 section JSON")
    parser.add_argument(
        "--diff",
        action="store_true",
        help="把输入/输出 JSON 的 unified diff 写到结果同目录的 .diff.txt 文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.input.is_file():
        print(f"输入文件不存在：{args.input}", file=sys.stderr)
        return 2

    try:
        section = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入 JSON 无法读取：{exc}", file=sys.stderr)
        return 2
    if not isinstance(section, dict):
        print("输入必须是单个 section JSON object，不是数组", file=sys.stderr)
        return 2
    if args.document_name is not None:
        section["document_name"] = args.document_name
    if args.section_name is not None:
        section["path"] = args.section_name

    reviewed = review_basic(
        section,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    original_text = json.dumps(section, ensure_ascii=False, indent=2) + "\n"
    reviewed_text = json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(reviewed_text, encoding="utf-8")
    logging.getLogger(__name__).info("结果已写出：%s", args.output)

    if args.diff:
        diff = difflib.unified_diff(
            original_text.splitlines(keepends=True),
            reviewed_text.splitlines(keepends=True),
            fromfile=str(args.input),
            tofile=str(args.output),
        )
        diff_path = args.output.with_suffix(".diff.txt")
        diff_path.write_text("".join(diff), encoding="utf-8")
        logging.getLogger(__name__).info("diff 已写出：%s", diff_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
