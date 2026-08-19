#!/usr/bin/env python3
"""快速调试单个 section 的基础 LLM 审校。

示例：
    python poc/review_basic.py \
        output/tongji_gaoshu_first1/31_习题_1–1.md \
        --document-name "同济大学高等数学第七版上册" \
        --output /tmp/review_1_1.md

默认只把审校结果打印到 stdout；日志打印到 stderr。输入文件开头的
Markdown 小标题会默认去掉，因为 review API 接收的是不含文件标题的 section 内容。
"""

from __future__ import annotations

import argparse
import difflib
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exercise_extractor.review import (  # noqa: E402
    DEFAULT_REVIEW_BASE_URL,
    DEFAULT_REVIEW_MODEL,
    review_markdown_basic,
)


def _strip_file_heading(content: str) -> tuple[str, str | None]:
    """去掉文件开头的第一个 Markdown 标题，返回正文和标题。"""
    match = re.match(r"\A\s*(#{1,6})\s+(.+?)\s*(?:\n|\Z)", content)
    if not match:
        return content, None
    return content[match.end() :].lstrip("\n"), match.group(2)


_PAGE_PREFIX_RE = re.compile(r"^\d+_")
_TAIL_SEARCH_SUFFIX_RE = re.compile(
    r"[（(]\s*(?:节尾习题|节尾搜索|结尾习题)\s*[）)]$"
)


def _section_name_from_path(path: Path) -> str:
    """从输出文件名去掉页码前缀和节尾搜索标记。"""
    name = _PAGE_PREFIX_RE.sub("", path.stem, count=1)
    return _TAIL_SEARCH_SUFFIX_RE.sub("", name).rstrip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="调试单个 Markdown section 的基础审校")
    parser.add_argument("input", type=Path, help="待审校的 section Markdown 文件")
    parser.add_argument(
        "--document-name",
        default=None,
        help="文档名称（默认读取 REVIEW_DOCUMENT_NAME，或使用输入文件父目录名）",
    )
    parser.add_argument(
        "--section-name",
        default=None,
        help="小节名称（默认使用输入文件名，不含扩展名）",
    )
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
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="可选：把结果另存为 Markdown 文件",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="把原文与审校结果的 unified diff 写到结果同目录的 .diff.txt 文件",
    )
    parser.add_argument(
        "--keep-heading",
        action="store_true",
        help="保留输入文件开头的小标题，不按默认行为剥离",
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
    if not args.output:
        print("需要指定output")
        return 2

    original = args.input.read_text(encoding="utf-8")
    if args.keep_heading:
        content = original
        detected_heading = None
    else:
        content, detected_heading = _strip_file_heading(original)
    document_name = (
        args.document_name
        or os.environ.get("REVIEW_DOCUMENT_NAME")
        or args.input.parent.name
    )
    section_name = args.section_name or _section_name_from_path(args.input)
    logging.getLogger(__name__).info(
        "开始单节基础审校：文件=%s，文档=%s，小节=%s，剥离标题=%s",
        args.input,
        document_name,
        section_name,
        detected_heading or "无",
    )

    reviewed = review_markdown_basic(
        content,
        document_name,
        section_name,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(reviewed, encoding="utf-8")
    logging.getLogger(__name__).info("结果已写出：%s", args.output)

    # sys.stdout.write(reviewed)
    # if reviewed and not reviewed.endswith("\n"):
    #     sys.stdout.write("\n")
    if args.diff:
        diff = difflib.unified_diff(
            content.splitlines(keepends=True),
            reviewed.splitlines(keepends=True),
            fromfile=str(args.input),
            tofile="basic_review",
        )
        diff_path = (
            args.output.with_suffix(".diff.txt")
            if args.output
            else args.input.with_suffix(".diff.txt")
        )
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text("".join(diff), encoding="utf-8")
        logging.getLogger(__name__).info("diff 已写出：%s", diff_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
