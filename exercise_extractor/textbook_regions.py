"""Locate numbered exercise headings from a reliable PDF text layer.

No OCR is performed here. Scanned PDFs retain the existing tail search.
"""

import re
from copy import deepcopy
from difflib import SequenceMatcher

from .parse_md_exercises import _section_heading_matches


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


def prepare_generic_numbered_sections(toc, document):
    """Recognize unnumbered Exercises headings followed by section-numbered questions."""
    found = {}
    for index, page in enumerate(document):
        text = page.get_text()
        for heading in re.finditer(r"(?im)^\s*Exercises\s*$", text):
            after = text[heading.end():heading.end() + 700]
            if not text[heading.end():].strip() and index + 1 < len(document):
                # The heading can be the last line on a page, with the first
                # question following the running header on the next page.
                after = document[index + 1].get_text()[:700]
            question = re.search(r"(?m)^\s*(\d+\.\d+)\.\d+\.\s+", after)
            if question:
                found.setdefault(question[1], index + 1)
    if len(found) < 2:
        return toc, {}
    result = deepcopy(toc)

    def walk(nodes):
        for node in nodes:
            match = re.match(r"^\s*(\d+\.\d+)\s", node.get("title", ""))
            if match and match[1] in found:
                # Third-level TOC entries are prose subtopics; the exercise
                # numbering identifies the parent as the exercise section.
                node['children'] = [child for child in node.get('children', [])
                                    if not re.match(r'^\s*' + re.escape(match[1]) + r'\.\d+\b', child.get('title', ''))]
            walk(node.get('children', []))
    walk(result)
    return result, found


def _generic_heading(text):
    """Find a standalone chapter-end exercise heading, tolerating text-layer OCR noise."""
    for line in re.finditer(r"^.*$", text, re.MULTILINE):
        normalized = re.sub(r"[^A-Za-z]", "", line.group()).upper()
        if not 7 <= len(normalized) <= 12 or not normalized.endswith("S"):
            continue
        if any(SequenceMatcher(None, normalized, label).ratio() >= 0.8
               for label in ("PROBLEMS", "EXERCISES")):
            return line
    return None


def _is_new_part(text, section_number):
    """Recognize a following chapter or back matter from the page opening."""
    opening = text[:300]
    if _BACK_MATTER.search(opening):
        return True
    chapter = _CHAPTER.search(opening)
    return bool(chapter and chapter[1] != section_number.split(".", 1)[0])


def _boundary_starts_at_page_top(page, title):
    """Use the PDF text layer when OCR omits a new chapter's opening title."""
    target = re.sub(r"[^a-z0-9]", "", title.lower())
    if not target:
        return False
    top_match = False
    later_match = False
    for block in page.get_text("blocks"):
        if block[6] != 0:
            continue
        block_text = re.sub(r"[^a-z0-9]", "", block[4].lower())
        match = (block_text.startswith(target)
                 or block_text.startswith("section" + target)
                 or _section_heading_matches(block[4], title))
        if match and block[1] <= page.rect.height * 0.2:
            top_match = True
        elif match:
            later_match = True
    # A running header can name the next project while the previous section's
    # exercises still fill the columns above its actual heading on this page.
    return top_match and not later_match


def locate_text_exercise_regions(sections, document, generic_numbered=None):
    retained = []
    for node in sections:
        if node.get("implicit"):
            number = re.match(r"^\s*(\d+(?:\.\d+)+)\b", node["title"])
            chapter_number = None if number else re.match(r"^\s*(\d+)\s+\S", node["title"])
            start = node.get("_section_start_page")
            boundary = node.get("boundary_page")
            unbounded = node.get("_unbounded_tail")
            generic_page = (generic_numbered or {}).get(number[1]) if number else None
            if generic_page and start <= generic_page <= (boundary or len(document)):
                end = min(boundary or len(document), len(document))
                if unbounded:
                    for page in range(generic_page + 1, end + 1):
                        if _is_new_part(document[page - 1].get_text(), number[1]):
                            end = page - 1
                            break
                node['exercise_page_range'] = [generic_page, end + 1]
                node['_generic_exercise_heading'] = True
                node['_exercise_heading_number'] = number[1]
                locate_text_exercise_regions(node.get('children', []), document, generic_numbered)
                retained.append(node)
                continue
            if generic_numbered and number and start and boundary:
                # Do not attribute the preceding section's numbered exercises
                # to this section when both share its opening page.
                opening = document[start - 1].get_text()
                prefixes = re.findall(r'(?m)^\s*(\d+\.\d+)\.\d+\.\s+', opening)
                section_text = '\n'.join(document[page - 1].get_text()
                                         for page in range(start, min(boundary, len(document)) + 1))
                own_questions = re.search(r'(?m)^\s*' + re.escape(number[1]) + r'\.\d+\.\s+', section_text)
                if prefixes and not own_questions and not _heading_pattern(number[1]).search(section_text):
                    continue
            if (number or chapter_number) and start and (boundary or unbounded):
                heading_number = (number or chapter_number)[1]
                pattern = _heading_pattern(heading_number)
                end = min(boundary or len(document), len(document))
                heading_page = None
                for page in range(start, end + 1):
                    page_text = document[page - 1].get_text()
                    if unbounded and _is_new_part(page_text, number[1]):
                        end = page - 1
                        break
                    if pattern.search(page_text) or (chapter_number and _generic_heading(page_text)):
                        heading_page = page
                        if not unbounded:
                            node["exercise_page_range"] = [page, end + 1]
                        node["_exercise_heading_number"] = heading_number
                        break
                if unbounded:
                    if heading_page is None:
                        continue
                    for page in range(heading_page + 1, len(document) + 1):
                        if _is_new_part(document[page - 1].get_text(), number[1]):
                            end = page - 1
                            break
                    node["exercise_page_range"] = [heading_page, end + 1]
                elif (
                    boundary <= len(document)
                    and node.get("boundary_title")
                    and _boundary_starts_at_page_top(
                        document[boundary - 1], node["boundary_title"]
                    )
                ):
                    # The boundary page starts the next chapter; its OCR may
                    # omit that title entirely, so the Markdown heading guard
                    # cannot stop its prose joining the final exercise.
                    first, last = node["exercise_page_range"]
                    if first < boundary < last:
                        node["exercise_page_range"] = [first, boundary]
        locate_text_exercise_regions(node.get("children", []), document, generic_numbered)
        if node.get("has_exercise") or node.get("is_answers") or node.get("children"):
            retained.append(node)
    sections[:] = retained


def truncate_to_exercise_heading(content, number):
    match = _heading_pattern(number).search(content)
    if match is None and number.isdigit():
        match = _generic_heading(content)
    if match is None:
        return content
    # Retain the source-page marker for exercise bbox and page attribution.
    markers = list(re.finditer(r"<!--\s*page:\s*\d+\s*-->", content[:match.start()]))
    prefix = markers[-1][0] + "\n" if markers else ""
    return prefix + content[match.start():]


def truncate_to_generic_exercise_heading(content):
    match = _generic_heading(content)
    if not match:
        return content
    markers = list(re.finditer(r"<!--\s*page:\s*\d+\s*-->", content[:match.start()]))
    return (markers[-1][0] + "\n" if markers else "") + content[match.start():]
