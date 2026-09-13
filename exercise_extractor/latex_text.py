"""将 LaTeX 文本转换为 Unicode 纯文本，供内部比较使用。"""

from functools import lru_cache

from pylatexenc.latex2text import LatexNodes2Text


@lru_cache(maxsize=1)
def _converter() -> LatexNodes2Text:
    # math_mode="text" 将公式内容转换为普通文本/Unicode，适合搜索与相似度比较。
    return LatexNodes2Text(math_mode="text")


def latex_to_unicode(text: str) -> str:
    """将 LaTeX 标记转成可比较的 Unicode 文本，不用于渲染或改写输出内容。"""
    if not text or not isinstance(text, str):
        return ""
    # 大多数题目文字不含 TeX 结构，跳过解析以降低 bbox 匹配阶段的开销。
    if "\\" not in text and "$" not in text:
        return text
    return _converter().latex_to_text(text)
