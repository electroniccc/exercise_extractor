"""exercise_extractor：从数学教材/习题集/试卷 PDF 中自动提取习题。"""

from .api import extract_exercise, extract_pdf
from .review import review_markdown_basic, review_markdown_with_tools

__version__ = "0.1.0"
__all__ = [
    "extract_exercise",
    "extract_pdf",
    "review_markdown_basic",
    "review_markdown_with_tools",
    "__version__",
]
