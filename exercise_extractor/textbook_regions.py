"""Locate numbered exercise headings from a reliable PDF text layer.

No OCR is performed here. Scanned PDFs retain the existing tail search.
"""

import re


def _heading_pattern(number):
    n = re.escape(number)
    decoration = r"(?:[^\S\r\n]|[#*])*"
    gap = r"[\s#*\x00-\x08\x0b\x0c\x0e-\x1f]+"
    return re.compile(
        rf"^{decoration}(?:{n}{gap}Exercises?|Exercises?{gap}{n}){decoration}$",
        re.IGNORECASE | re.MULTILINE,
    )


_BACK_MATTER = re.compile(r"^\s*(?:APPENDIX|APPENDICES|INDEX|BIBLIOGRAPHY|REFERENCES)\b", re.IGNORECASE | re.MULTILINE)
_CHAPTER = re.compile(r"^\s*CHAPTER\s+(\d+)\b", re.IGNORECASE | re.MULTILINE)


def _is_new_part(text, section_number):
    """Recognize a following chapter or back matter from the page opening."""
    opening = text[:300]
    if _BACK_MATTER.search(opening):
        return True
    chapter = _CHAPTER.search(opening)
    return bool(chapter and chapter[1] != section_number.split(".", 1)[0])


def locate_text_exercise_regions(sections, document):
    retained = []
    for node in sections:
        if node.get("implicit"):
            number = re.match(r"^\s*(\d+(?:\.\d+)+)\b", node["title"])
            start = node.get("_section_start_page")
            boundary = node.get("boundary_page")
            unbounded = node.get("_unbounded_tail")
            if number and start and (boundary or unbounded):
                pattern = _heading_pattern(number[1])
                end = min(boundary or len(document), len(document))
                heading_page = None
                for page in range(start, end + 1):
                    page_text = document[page - 1].get_text()
                    if unbounded and _is_new_part(page_text, number[1]):
                        end = page - 1
                        break
                    if pattern.search(page_text):
                        heading_page = page
                        if not unbounded:
                            node["exercise_page_range"] = [page, end + 1]
                        node["_exercise_heading_number"] = number[1]
                        break
                if unbounded:
                    if heading_page is None:
                        continue
                    for page in range(heading_page + 1, len(document) + 1):
                        if _is_new_part(document[page - 1].get_text(), number[1]):
                            end = page - 1
                            break
                    node["exercise_page_range"] = [heading_page, end + 1]
        locate_text_exercise_regions(node.get("children", []), document)
        if node.get("has_exercise") or node.get("is_answers") or node.get("children"):
            retained.append(node)
    sections[:] = retained


def truncate_to_exercise_heading(content, number):
    match = _heading_pattern(number).search(content)
    if match is None:
        return content
    # Retain the source-page marker for exercise bbox and page attribution.
    markers = list(re.finditer(r"<!--\s*page:\s*\d+\s*-->", content[:match.start()]))
    prefix = markers[-1][0] + "\n" if markers else ""
    return prefix + content[match.start():]
