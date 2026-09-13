import hashlib
import os
import re

import cv2
import numpy as np
import pymupdf
from PIL import Image

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def image_from_page(page: pymupdf.Page) -> np.ndarray:
    mat = pymupdf.Matrix(2, 2)
    pm = page.get_pixmap(matrix=mat, alpha=False)
    if pm.width > 2000 or pm.height > 2000:
        pm = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
    img = Image.frombytes("RGB", [pm.width, pm.height], pm.samples)
    img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return img


def compute_file_hash(filepath: str) -> str:
    """SHA256 hex digest of a file, truncated to 32 hex characters."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()[:32]


def fix_markdown_block_latex(markdown_file_path: str) -> None:
    """修 PaddleOCR-VL 的小 bug：$$ 块公式首尾带空格，VSCode preview 渲染不了。

    仅方便本地调试；不影响后续切分逻辑。
    """
    for f in os.listdir(markdown_file_path):
        if os.path.isfile(os.path.join(markdown_file_path, f)) and f.endswith(".md"):
            file_path = os.path.join(markdown_file_path, f)
            with open(file_path, "r+", encoding="utf-8", errors="ignore") as file:
                content = file.read()

                def replacer(match):
                    item = match.group(1)
                    return f"{item}\n"

                content = re.sub(r"\s(\$\$.+?\$\$)\s\n", replacer, content)
                file.seek(0)
                file.write(content)
                file.truncate()
