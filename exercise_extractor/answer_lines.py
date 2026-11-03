"""Conservative image detection for repeated blank answer lines on exam pages."""

from __future__ import annotations

from collections import Counter
import re

import cv2
import numpy as np


_REPEATED_SLASHES = re.compile(r"(?:\\\s*){80,}")
_REPEATED_CHARACTER = re.compile(r"([^\s])\1{100,}")


def is_repetition_garbage(markdown: str) -> bool:
    """Detect the very long repeated output produced from blank writing lines."""
    if _REPEATED_SLASHES.search(markdown) or _REPEATED_CHARACTER.search(markdown):
        return True
    if len(markdown) < 1500:
        return False
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if len(lines) < 80:
        return False
    counts = Counter(lines).most_common(3)
    return counts[0][1] >= 30 and sum(count for _, count in counts) / len(lines) >= .75


def mask_answer_lines(image: np.ndarray) -> tuple[np.ndarray, int]:
    """Whiten only groups of long, dotted horizontal lines on a page image.

    A lone line may be an equation bar, diagram edge or underline. At least
    three similarly aligned, regularly spaced dotted lines are required.
    """
    if not isinstance(image, np.ndarray) or image.ndim < 2:
        return image, 0
    height, width = image.shape[:2]
    if height < 100 or width < 100:
        return image, 0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    ink = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]
    # Join small gaps between dots, then keep only long horizontal strokes.
    joined = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, int(width * .016)), 1)),
    )
    horizontal = cv2.morphologyEx(
        joined, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, int(width * .38)), 1)),
    )
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * .5 or h > max(4, height * .005):
            continue
        # A solid rule is likely part of a table or diagram, not a writing line.
        fill = np.count_nonzero(ink[y:y+h, x:x+w]) / (w * h)
        if fill > .65:
            continue
        candidates.append((x, y, w, h))
    candidates.sort(key=lambda box: box[1])
    groups: list[list[tuple[int, int, int, int]]] = []
    for box in candidates:
        previous = groups[-1][-1] if groups else None
        if (previous and abs(box[0] - previous[0]) <= width * .02
                and abs(box[2] - previous[2]) <= width * .03
                and height * .015 <= box[1] - previous[1] <= height * .08):
            groups[-1].append(box)
        else:
            groups.append([box])
    selected = [box for group in groups if len(group) >= 3 for box in group]
    if not selected:
        return image, 0
    cleaned = image.copy()
    for x, y, w, h in selected:
        cleaned[max(0, y-2):min(height, y+h+2), max(0, x-2):min(width, x+w+2)] = 255
    return cleaned, len(selected)
