"""Expand OCR math arrays that contain several numbered exercises."""

import re


_ARRAY = re.compile(
    r"\$\$\s*\\begin\{array\}\{[^{}]*\}(.*?)\\end\{array\}\s*\$\$",
    re.DOTALL,
)
_NUMBER = re.compile(
    r"^(?:\\(?:mathbf|textbf|textcircled)\{(\d{1,3})\}|(\d{1,3}))"
    r"\s*[.．、)]\s*~?\s*",
)
_RANGE = re.compile(r"(?m)^\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})(?=\s|$)")


def _cells(body: str) -> list[str]:
    cells = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return []
        elif char == "&" and depth == 0:
            cells.append(body[start:index])
            start = index + 1
    if depth:
        return []
    cells.append(body[start:])
    return cells


def _unwrap(cell: str) -> str:
    cell = cell.strip()
    if cell.startswith("{") and cell.endswith("}"):
        return cell[1:-1].strip()
    return cell


def expand_numbered_math_arrays(markdown: str) -> str:
    """Keep ordinary arrays intact; expand only clear ascending question lists."""
    def replace(match: re.Match[str]) -> str:
        # Only a nearby exercise range supplies enough context to distinguish
        # numbered questions from numbered identities or steps in the prose.
        ranges = list(_RANGE.finditer(markdown[max(0, match.start() - 2000):match.start()]))
        if not ranges:
            return match[0]
        first, last = map(int, ranges[-1].groups())
        body = match[1]
        if r"\\" in body:
            return match[0]
        cells = _cells(body)
        if not cells:
            return match[0]
        groups: list[tuple[int, list[str]]] = []
        for raw in cells:
            cell = _unwrap(raw)
            marker = _NUMBER.match(cell)
            if marker:
                groups.append((int(marker[1] or marker[2]), []))
                cell = cell[marker.end():]
            if cell and cell != "{}" and groups:
                groups[-1][1].append(cell)
        numbers = [number for number, _ in groups]
        if (len(groups) < 2 or numbers != sorted(set(numbers))
                or not all(first <= number <= last for number in numbers)
                or any(not parts for _, parts in groups)):
            return match[0]
        return "\n\n".join(
            f"{number}. $" + r"\quad ".join(parts).rstrip() + "$"
            for number, parts in groups
        )

    return _ARRAY.sub(replace, markdown)
