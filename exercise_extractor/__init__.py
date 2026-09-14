"""exercise_extractor：从数学教材/习题集/试卷 PDF 中自动提取习题。"""

from .api import (
    extract_exercise,
    extract_exercise_from_image,
    extract_exercise_from_markdown,
    extract_pdf,
    reparse_exercises,
)
from .answers import match_answers
from .llm_usage import LlmUsage
from .review import review_basic

__version__ = "0.1.0"
__all__ = [
    "extract_exercise",
    "extract_exercise_from_image",
    "extract_exercise_from_markdown",
    "extract_pdf",
    "reparse_exercises",
    "match_answers",
    "LlmUsage",
    "review_basic",
    "__version__",
]
