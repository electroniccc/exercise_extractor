"""HTML 表格检测与转 markdown 表格。

OCR 直接输出的 HTML 表格（<table>...</table>）在大多数 markdown 预览器里
不能正常渲染单元格内的 LaTeX，转成 markdown 风格表格后可正常显示。
"""

import re

# 表格块：<table ...>...</table>（OCR 的表格通常是一整行）
_TABLE_BLOCK_RE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)
# 标签：开/闭/自闭合
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")
# 无闭合标签的 void 元素
_VOID_TAGS = {
    "br", "img", "hr", "input", "meta", "link", "area", "base",
    "col", "embed", "source", "track", "wbr",
}


def _is_well_formed_table(html: str) -> bool:
    """表格块内部的 HTML 标签是否严格闭合（栈式校验）。"""
    stack: list[str] = []
    for m in _TAG_RE.finditer(html):
        closing, tag, self_closing = m.group(1), m.group(2).lower(), m.group(3)
        if self_closing or tag in _VOID_TAGS:
            continue
        if closing:
            if not stack or stack[-1] != tag:
                return False
            stack.pop()
        else:
            stack.append(tag)
    return not stack


def detect_html_table(markdown_content: str) -> bool:
    """内容中是否存在 HTML 格式的表格，且内部标签严格闭合。"""
    return any(
        _is_well_formed_table(block)
        for block in _TABLE_BLOCK_RE.findall(markdown_content)
    )


def _clean_cell(html: str) -> str:
    """清理单元格：保留 <br>，去掉其余 HTML 标签，折叠空白，转义管道符。"""
    text = re.sub(r"<br\s*/?>", "<br>", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("|", "\\|")


def html_table_to_markdown(html_table: str) -> str:
    """把单个 HTML 表格转成 markdown 风格表格（GFM）。

    第一个 <table> 块生效；第一行作为表头；单元格内的 LaTeX（$...$）原样保留。
    """
    m = _TABLE_BLOCK_RE.search(html_table)
    if not m:
        return html_table
    block = m.group(0)
    rows: list[list[str]] = []
    for tr in re.findall(
        r"<tr\b[^>]*>(.*?)</tr\s*>", block, re.DOTALL | re.IGNORECASE
    ):
        cells = [
            _clean_cell(td)
            for td in re.findall(
                r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", tr, re.DOTALL | re.IGNORECASE
            )
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return html_table
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def replace_html_tables(markdown_content: str) -> str:
    """把内容里严格闭合的 HTML 表格替换成 markdown 表格；其余原样保留。"""
    if not detect_html_table(markdown_content):
        return markdown_content

    def repl(m: re.Match[str]) -> str:
        block = m.group(0)
        if _is_well_formed_table(block):
            return html_table_to_markdown(block)
        return block

    return _TABLE_BLOCK_RE.sub(repl, markdown_content)
