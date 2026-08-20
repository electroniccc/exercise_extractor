"""exercise_extractor：从数学教材/习题集/试卷 PDF 中自动提取习题。"""

from .api import extract_exercise, extract_pdf
from .answers import match_answers
from .review import review_basic

__version__ = "0.1.0"
__all__ = [
    "extract_exercise",
    "extract_pdf",
    "match_answers",
    "review_basic",
    "__version__",
]
