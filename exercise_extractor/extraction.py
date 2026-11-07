"""从目录树 + OCR 原始页到“习题区域/结构化习题”的整条提取链。

把 main.py 里的这层逻辑独立出来，便于后续在提取链上插入 LLM 检查/审核：
- build_section_tree：目录树 → 习题区域（小节树，含页码范围、隐式节尾节点、
  习题答案节点）；
- collect_pages_to_ocr：习题区域 → 需要 OCR 的页码；
- combine_section_pages：把各区域的原始 OCR 页拼成 node["exercise_content"]；
- extract_exercises：把区域内容切成结构化习题（截断、还原、过滤、重编号）。

main.py 只负责 CLI、目录识别调用、OCR、答案章节匹配和导出。
"""

import logging
import os
import re
from typing import TypedDict

from . import answers
from .html_tables import replace_html_tables
from .ocr_cache import page_cache_is_ready
from .parse_md_exercises import (
    Exercise,
    demote_exercise_headings,
    filter_exercises_by_section,
    is_generic_section,
    normalize_section,
    numbers_are_consecutive,
    page_marker,
    split_exercises,
    split_solutions_for_section,
    truncate_before_heading,
    truncate_before_page_heading,
    truncate_to_section_heading,
)
from .toc_detect import (
    TocNode,
    is_answers_title,
    is_exercise_title,
    is_skippable_title,
    should_use_tail_search,
)

logger = logging.getLogger(__name__)

# 图注/图引用："图 2-12"、"图2-12"、"图2.12" 等
_FIGURE_REF_RE = re.compile(r"图\s*(\d{1,2})\s*[-—–.．]\s*(\d{1,2})")
_ENGLISH_FIGURE_REF_RE = re.compile(
    r"\b(?:figure|fig\.?)\s*(\d{1,3})(?:\s*[-.．]\s*(\d{1,3}))?\b",
    re.IGNORECASE,
)
# 图片行：<div ...><img src="..." /></div> 等
_IMAGE_LINE_RE = re.compile(r"<img\s+[^>]*src=\"[^\"]+\"[^>]*>")
# 子图标签行：<div ...>(a)</div>、"（1）" 等，夹在子图之间、图注之前
_SUBLABEL_RE = re.compile(
    r"^\s*(?:<div\b[^>]*>\s*)?[（(]\s*[a-zA-Z0-9一二三四五六七八九十]+\s*[）)]"
    r"(?:\s*</div>)?\s*$"
)
# 图注位置占位标记（图注行被抽走后留在流程里，供切分后定位归属）
_FIGURE_MARKER_RE = re.compile(r"<!--\s*figure:\s*(.+?)\s*-->")


class SectionNode(TypedDict, total=False):
    """小节树节点：章节 / 小节 / 习题条目 / 隐式节尾习题节点 / 习题答案节点。"""

    title: str
    has_exercise: bool
    children: list["SectionNode"]
    exercise_page_range: list[int]
    implicit: bool
    is_answers: bool
    tail_boundary_title: str
    exercise_content: str
    exercises: list[Exercise]
    answer_sections: list[answers.AnswerSection]
    content: dict[str, str]


def _figure_caption_key(line: str) -> str | None:
    """识别图注行并返回图键；不是图注返回 None。

    支持 "图 2-12"、"图2.12"、"补充例题1.3题图"、"例3.2题图" 等格式；
    "题图" 后缀用于把"补充例题1.3题图"与普通文字行区分开。
    """
    text = re.sub(r"<[^>]+>", "", line).strip()
    if not text:
        return None
    m = re.fullmatch(r"图\s*(\d{1,2})\s*[-—–.．]\s*(\d{1,2})\s*(题图)?", text)
    if m:
        return f"图{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(
        r"(?:figure|fig\.?)\s*(\d{1,3})(?:\s*[-.．]\s*(\d{1,3}))?",
        text, re.IGNORECASE,
    )
    if m:
        return f"Figure{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
    m = re.fullmatch(
        r"([\u4e00-\u9fff]{1,10}?)(\d{1,2}(?:[.．]\d{1,2})+)\s*题图", text
    )
    if m:
        return f"{m.group(1)}{m.group(2).replace('．', '.')}"
    return None


def extract_figures(content: str) -> tuple[str, dict[str, list[str]]]:
    """抽出内容里的全部配图，按图片下方的图注归类。

    返回 (带图注占位标记的内容, {图键: [图片行..., 图注行]})。
    紧跟图片的图注（允许空行和子图标签）归到同一图组，图注位置留占位
    标记，切分后按占位标记或题内引用把图插回。没有图注的图片保留原位，
    由题号切分决定归属。
    """
    figures: dict[str, list[str]] = {}
    pending: list[str] = []
    kept: list[str] = []
    lines = content.split("\n")
    # OCR 有时将独立图注放在图片前面。只调整紧邻图片的独立图注，
    # 避免把正文中的 Figure 引用当作图注移动。
    index = 0
    while index < len(lines):
        if _figure_caption_key(lines[index]) is None:
            index += 1
            continue
        previous = index - 1
        while previous >= 0 and not lines[previous].strip():
            previous -= 1
        if previous >= 0 and _IMAGE_LINE_RE.search(lines[previous]):
            index += 1
            continue
        image_at = index + 1
        while image_at < len(lines) and not lines[image_at].strip():
            image_at += 1
        if image_at < len(lines) and _IMAGE_LINE_RE.search(lines[image_at]):
            caption = lines.pop(index)
            lines.insert(image_at, caption)
            index = image_at + 1
        else:
            index += 1
    for line in lines:
        if _IMAGE_LINE_RE.search(line):
            pending.append(line)
            continue
        if pending and (not line.strip() or _SUBLABEL_RE.match(line)):
            # 空行和子图标签随当前图组走；遇到下一道题等正文时立即结算，
            # 避免把无图注图片跨题归给后面的图注。
            pending.append(line)
            continue
        key = _figure_caption_key(line)
        if key is not None and pending:
            figures.setdefault(key, []).extend(pending)
            figures[key].append(line)  # 图注行跟图片一起走
            kept.append(f"<!-- figure: {key} -->")
            pending = []
            continue
        kept.extend(pending)
        pending = []
        kept.append(line)
    kept.extend(pending)
    return "\n".join(kept), figures


def _attach_figures_to_text(
    text: str,
    figures: dict[str, list[str]],
    attached: set[str],
    mentions: bool,
    markers: bool,
) -> str:
    """把一段文本里的图引用/图占位标记替换为对应的图片行+图注行。

    mentions/markers 分别控制是否处理题内“图N-M”引用和图注占位标记，
    用于两遍处理：先按题内引用优先附图，再按占位标记兜底。
    """
    lines = text.split("\n")
    result: list[str] = []
    changed = False
    for line in lines:
        m_marker = _FIGURE_MARKER_RE.match(line.strip())
        if m_marker:
            if not markers:
                result.append(line)
                continue
            key = m_marker.group(1).strip()
            if key in figures and key not in attached:
                result.append("")
                result.extend(figures[key])
                attached.add(key)
            changed = True  # 占位标记无论是否附图都不留在文本里
            continue
        result.append(line)
        if not mentions:
            continue
        keys = [f"图{m.group(1)}-{m.group(2)}" for m in _FIGURE_REF_RE.finditer(line)]
        keys.extend(
            f"Figure{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")
            for m in _ENGLISH_FIGURE_REF_RE.finditer(line)
        )
        for key in keys:
            if key in figures and key not in attached:
                result.append("")
                result.extend(figures[key])
                attached.add(key)
                changed = True
    return "\n".join(result) if changed else text


def attach_figures(
    exercises: list[Exercise], figures: dict[str, list[str]]
) -> None:
    """把抽出的配图插回对应习题。

    第一遍按题干提到的“图N-M”（如图 2-12）插到引用行之后（引用优先）；
    第二遍把未被引用的图按切分前图注位置留下的占位标记插回（如理论力学的
    “补充例题1.3题图”，图在 [解] 流程中间）。每张图只附一次。
    """
    if not figures:
        return
    attached: set[str] = set()
    fields = ("content", "solution_steps", "answer")
    # 第一遍：题内引用优先
    for item in exercises:
        for field in fields:
            text = item.get(field)
            if not text:
                continue
            new_text = _attach_figures_to_text(
                text, figures, attached, mentions=True, markers=False
            )
            if new_text != text:
                item[field] = new_text
    # 第二遍：未被引用的图按图注占位标记兜底
    for item in exercises:
        for field in fields:
            text = item.get(field)
            if not text:
                continue
            new_text = _attach_figures_to_text(
                text, figures, attached, mentions=False, markers=True
            )
            if new_text != text:
                item[field] = new_text


def convert_exercise_tables(exercises: list[Exercise]) -> None:
    """把习题内容里的 HTML 表格替换成 markdown 表格（单元格内 LaTeX 才能正常渲染）。"""
    for item in exercises:
        for field in ("content", "solution_steps", "answer"):
            text = item.get(field)
            if not text:
                continue
            new_text = replace_html_tables(text)
            if new_text != text:
                item[field] = new_text


def build_section_tree(
    toc_tree: list[TocNode],
    doc_type: str = "textbook",
    doc_page_count: int | None = None,
) -> list[SectionNode]:
    """从目录树中筛出习题章节，返回小节树。

    doc_type:
    - textbook:     教材。除了目录里有习题项的情况，有些课本每小节末尾直接附习题
                    （没有目录项）。是否对叶小节末尾取几页做隐式搜索由
                    should_use_tail_search 智能判断（大多数小节已有目录习题项则不搜）。
    - exercise_book / exampaper: 暂用目录关键词检测，专用策略后续实现。

    小节树节点：{"title", "has_exercise", "children", "exercise_page_range": [start, end)}

    doc_page_count：文档总页数。“习题答案”类节点没有 boundary 时用它收尾
    （答案内容默认持续到文档最后一页）。
    """
    use_tail_search = should_use_tail_search(toc_tree, doc_type)
    logger.info(
        "doc_type=%s, 节尾搜索=%s", doc_type, "开" if use_tail_search else "关"
    )
    # Part 等无页码目录项可能隔开相邻章节。跨层级找下一章，供最后
    # 一个章节的章末习题确定边界；普通同级边界仍优先使用。
    numbered_chapters: list[TocNode] = []

    def collect_numbered_chapters(nodes: list[TocNode]) -> None:
        for entry in nodes:
            if (
                re.match(r"^\s*\d+\s+\S", entry.get("title", ""))
                and isinstance(entry.get("page_num"), int)
                and not is_exercise_title(entry["title"])
                and not is_skippable_title(entry["title"])
            ):
                numbered_chapters.append(entry)
            collect_numbered_chapters(entry.get("children", []))

    collect_numbered_chapters(toc_tree)
    next_chapter = {
        id(current): following
        for current, following in zip(numbered_chapters, numbered_chapters[1:])
        if following["page_num"] > current["page_num"]
    }

    def get_exercise_section(
        node: TocNode,
        node_next: TocNode | None,
        ancestor_next: TocNode | None,
    ) -> SectionNode | None:
        title = node["title"]
        # 本节点的结束边界 = 下一个兄弟节点；若为最后一个子节点，则上溯取
        # 最近一层祖先的 next（边界逐级下传，支持任意层级深度）
        boundary = node_next or ancestor_next
        if not isinstance((boundary or {}).get("page_num"), int):
            boundary = next_chapter.get(id(node), boundary)
        # 习题答案类目录项（习题答案/参考答案/答案与提示/习题解答）：
        # 整节都是答案，单独处理，不参与普通习题切分
        if is_answers_title(title):
            start_page = node.get("page_num")
            if not isinstance(start_page, int) or start_page < 1:
                logger.warning(
                    "习题答案节点缺少有效页码，跳过: %s (page_num=%r)",
                    title,
                    start_page,
                )
                return None
            # 有 boundary 时以下一节点第一页收尾；否则默认到文档最后一页
            end_page = None
            if (
                boundary
                and isinstance(boundary.get("page_num"), int)
                and boundary["page_num"] > start_page
            ):
                end_page = boundary["page_num"]
            elif doc_page_count:
                end_page = doc_page_count + 1
            else:
                end_page = start_page + 3
            return {
                "title": title,
                "is_answers": True,
                "children": [],
                "exercise_page_range": [start_page, max(start_page + 1, end_page)],
            }
        # 序/前言/附录等前后置内容：完全跳过
        if is_skippable_title(title):
            return None
        result = {"title": title, "has_exercise": False, "children": []}
        title_lower = title.lower()
        if is_exercise_title(title):
            start_page = node["page_num"]
            if not isinstance(start_page, int) or start_page < 1:
                # toc_forge 的 LLM 可能给 page_num 为 null，此时无法定位页面，跳过该节点
                logger.warning(
                    "习题节点缺少有效页码，跳过: %s (page_num=%r)", title, start_page
                )
            else:
                end_page = start_page + 3
                # 用边界节点的页码收尾：含边界页（习题可能印到下一节标题所在页
                # 的顶部，如高数习题1-1 的 17、18 题在"第二节"标题页），
                # 串入的下一节内容由 extract_exercises 按边界标题截断。
                # 目录树顺序可能与页码顺序不一致，可能出现边界页码 <= 当前页
                if (
                    boundary
                    and isinstance(boundary.get("page_num"), int)
                    and boundary["page_num"] > start_page
                ):
                    end_page = boundary["page_num"] + 1
                # 保证至少覆盖一页（防御 start == end 时 range() 为空）
                end_page = max(end_page, start_page + 1)
                result["has_exercise"] = True
                result["exercise_page_range"] = [start_page, end_page]
                result["boundary_title"] = boundary.get("title") if boundary else None
                if boundary and isinstance(boundary.get("page_num"), int):
                    result["boundary_page"] = boundary["page_num"]
        if "children" in node and len(node["children"]) > 0:
            for idx in range(len(node["children"])):
                child_node = node["children"][idx]
                next_child_node = None
                if (idx + 1) < len(node["children"]):
                    next_child_node = node["children"][idx + 1]
                child_result = get_exercise_section(
                    child_node, next_child_node, boundary
                )
                if child_result:
                    result["children"].append(child_result)

        # 教材模式：小节末尾可能直接附习题（但没有目录项）——
        # 对每个非习题的叶节点，把“下一节点页码”（含该页）作为小节最后一页，
        # 往前取 3 页，建一个隐式习题节点，用同样的切分逻辑尝试提取。
        # 边界页（下一目录节点的起始页）要搜：不少教材把习题印在下一节标题
        # 所在页的顶部（如 §1.1 的习题与 §1.2 的标题同页）。串入的下一节内容
        # 由 extract_exercises 里的“截断到边界标题行”处理。
        tail_children = node.get("children", [])
        skippable_children = [
            child for child in tail_children if is_skippable_title(child["title"])
        ]
        tail_boundary = boundary
        numbered_section = bool(re.match(r"^\s*\d+(?:\.\d+)+\b", title))
        # A numbered section may contain a project or other subentry after its
        # own exercises.  Use that first child as the exercise search boundary.
        if numbered_section and isinstance(node.get("page_num"), int):
            for child in tail_children:
                child_page = child.get("page_num")
                if (isinstance(child_page, int)
                    and child_page > node["page_num"]
                    and (not tail_boundary
                         or not isinstance(tail_boundary.get("page_num"), int)
                         or child_page < tail_boundary["page_num"])):
                    tail_boundary = child
        for child in skippable_children:
            child_page = child.get("page_num")
            if (
                isinstance(child_page, int)
                and isinstance(node.get("page_num"), int)
                and child_page > node["page_num"]
                and (
                    not tail_boundary
                    or not isinstance(tail_boundary.get("page_num"), int)
                    or child_page < tail_boundary["page_num"]
                )
            ):
                tail_boundary = child
        if (
            use_tail_search
            and not result["has_exercise"]
            and (not tail_children or len(skippable_children) == len(tail_children)
                 or (numbered_section and not any(
                     is_exercise_title(child["title"]) for child in tail_children
                 )))
            and isinstance(node.get("page_num"), int)
            and (
                (tail_boundary and isinstance(tail_boundary.get("page_num"), int))
                or (doc_page_count and re.match(r"^\s*\d+(?:\.\d+)+\b", title))
            )
        ):
            has_boundary = bool(tail_boundary and isinstance(tail_boundary.get("page_num"), int))
            boundary_page = tail_boundary["page_num"] if has_boundary else doc_page_count
            # Without a mapped following TOC entry, create a candidate only.
            # The PDF text-layer pass must find this section's Exercises heading
            # and a safe end before any pages are sent to OCR.
            tail_start = max(node["page_num"], boundary_page - 3) if has_boundary else node["page_num"]
            if boundary_page >= tail_start:
                # 结束边界取 boundary_page + 1（含边界页），保证至少覆盖一页
                tail_end = max(tail_start + 1, boundary_page + 1)
                implicit_node = {
                        "title": title,
                        "has_exercise": True,
                        "implicit": True,
                        "_section_start_page": node["page_num"],
                        "children": [],
                        "exercise_page_range": [tail_start, tail_end],
                        "boundary_title": tail_boundary.get("title") if has_boundary else None,
                        "boundary_page": boundary_page if has_boundary else None,
                        "_unbounded_tail": not has_boundary,
                    }
                # The section's exercises precede any project or reading
                # subentry, including when both begin on one PDF page.
                if numbered_section and tail_children:
                    result["children"].insert(0, implicit_node)
                else:
                    result["children"].append(implicit_node)
        if result["has_exercise"] == False and len(result["children"]) == 0:
            return None
        return result

    sections: list[SectionNode] = []
    for idx in range(len(toc_tree)):
        node = toc_tree[idx]
        node_next: TocNode | None = None
        if (idx + 1) < len(toc_tree):
            node_next = toc_tree[idx + 1]
        section_result = get_exercise_section(node, node_next, None)
        if section_result:
            sections.append(section_result)
    # 记录本次运行是否启用了节尾搜索：写入每个节点，
    # 便于在内部结果中查看（隐式节点 implicit=True 即节尾搜索产物）
    def mark_tail_search(nodes: list[SectionNode]) -> None:
        for n in nodes:
            n["tail_search_used"] = use_tail_search
            mark_tail_search(n.get("children", []))

    mark_tail_search(sections)
    return sections


def collect_pages_to_ocr(
    sections: list[SectionNode], markdown_path: str
) -> list[int]:
    """遍历小节树，返回所有尚未完整发布（含 READY）的习题页。"""
    pages: set[int] = set()

    def walk(node: SectionNode) -> None:
        if node.get("has_exercise") or node.get("is_answers"):
            start_page, end_page = node["exercise_page_range"]
            for page_num in range(start_page, end_page):
                if not page_cache_is_ready(markdown_path, page_num):
                    # 失败页及缺少 READY 的半成品下次仍会重跑 OCR。
                    pages.add(page_num)
        for child_node in node.get("children", []):
            walk(child_node)

    for section_node in sections:
        walk(section_node)
    return sorted(pages)


def rewrite_img_path(content: str, page_num: int, markdown_path: str) -> str:
    """把页内图片的相对路径改成绝对路径，方便直接预览。"""

    def replacer(match: re.Match[str]) -> str:
        item = match.group(1)
        new_path = os.path.join(
            markdown_path, f"page_{page_num}", "imgs", os.path.basename(item)
        )
        return f'<img src="{os.path.abspath(new_path)}" />'

    return re.sub(r'<img\s+src="(.*?)".*?>', replacer, content)


def combine_section_pages(
    sections: list[SectionNode], markdown_path: str
) -> None:
    """把每个习题章节的若干页 markdown 拼成 node["exercise_content"]。

    页与页之间插入 "<!-- page: N -->" 标记，供切分时做页码溯源。
    """

    def walk(node: SectionNode) -> None:
        if node.get("has_exercise") or node.get("is_answers"):
            start_page, end_page = node["exercise_page_range"]
            exercise_content = ""
            for page_num in range(start_page, end_page):
                page_markdown_path = os.path.join(
                    markdown_path, f"page_{page_num}", "page.md"
                )
                if page_cache_is_ready(markdown_path, page_num):
                    with open(
                        page_markdown_path, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        content = f.read()
                    if node.get("_exercise_heading_number"):
                        from .textbook_reading_order import ordered_exercise_page
                        content = ordered_exercise_page(
                            markdown_path, page_num, node, content
                        )
                    content = rewrite_img_path(content, page_num, markdown_path)
                    exercise_content += f"\n{page_marker(page_num)}\n{content}\n"
            node["exercise_content"] = exercise_content
        for child_node in node.get("children", []):
            walk(child_node)

    for section_node in sections:
        walk(section_node)


def extract_exercises(
    sections: list[SectionNode],
    known_sections: set[str] | None = None,
) -> None:
    """对每个习题章节执行切分，结果写入 node["exercises"]。

    相邻小节的页码范围可能重叠（如“习题1-10”与“总习题一”都从第 85 页开始），
    切分器会按页内小节标题给每道题打上 section 归属，这里再按节点自己的
    小节名过滤，只保留真正属于本小节的习题；若 OCR 漏掉小节标题（没有任何
    习题带上本节点的小节名），则保留全部，避免误删。

    隐式节尾习题节点（implicit）若没提取到任何习题（如介绍性内容、纯参考页），
    直接从树中移除，不进入输出。

    known_sections：预计算的“树中所有小节名”集合（流式提取时传入，
    保证隐式节点过滤行为与整树一次提取一致）；不传则从 sections 自行收集。

    后续接入 LLM 检查/审核时，可在此函数内（或其后）对 node["exercises"]
    做二次校验过滤。
    """
    # 树中所有已知小节名（归一化）：用于剔除隐式节点里串入的“下一节”编号内容
    if known_sections is None:
        known_sections = set()

        def collect_titles(node: SectionNode) -> None:
            if node.get("title"):
                known_sections.add(normalize_section(node["title"]))
            for child_node in node.get("children", []):
                collect_titles(child_node)

        for section_node in sections:
            collect_titles(section_node)

    def walk(
        node: SectionNode, parent_title: str | None = None
    ) -> SectionNode | None:
        if node.get("has_exercise"):
            content = node.get("exercise_content", "")
            if node.get("implicit"):
                # 节尾防御：小节较短时，节尾区间会前伸到上一节末尾（如把上一节
                # 习题的最后一题包进来）。找到本小节标题所在行，只取其后的内容。
                if parent_title:
                    content = truncate_to_section_heading(content, parent_title)
                if node.get("_generic_exercise_heading"):
                    from .textbook_regions import truncate_to_generic_exercise_heading
                    content = truncate_to_generic_exercise_heading(content)
                elif node.get("_exercise_heading_number"):
                    from .textbook_regions import truncate_to_exercise_heading
                    content = truncate_to_exercise_heading(content, node["_exercise_heading_number"])
            else:
                # 目录页码可能落在上一节正文上（如页面上半是例题、下半才是
                # "## 习题"）：从本节点自己的标题行开始截取，避免正文混入第一题。
                content = truncate_to_section_heading(content, node["title"])
            # 边界防御：区间含下一目录节点的起始页（习题常印在下一节标题所在页
            # 的顶部）。找到边界节点标题行，丢弃其后的内容，避免把下一节的内容
            # 当成本节的习题。
            if node.get("boundary_title"):
                content = truncate_before_heading(content, node["boundary_title"])
            # 边界标题写法可能与页内标题不一致：按边界页第一个标题行兜底截断
            if node.get("boundary_page") and not node.get("_generic_exercise_heading"):
                content = truncate_before_page_heading(
                    content, node["boundary_page"]
                )
            if not node.get("implicit"):
                # OCR 可能把题号行渲染成标题（如 "### 7.5 设氢原子的状态是"），
                # 还原成题号行，避免丢题、并把后续题目归到错误小节。
                content = demote_exercise_headings(content)
            # 留存边界截取后的 OCR 原文供 WebUI 的 AI 整节审校使用；正式题目
            node["_review_source_markdown"] = content
            # 先抽出配图（按图注归类），避免图片因排版位置被切进别的题
            content, figures = extract_figures(content)
            # 隐式节尾节点：区间刻意从上一节正文中段开始，第一题前的正文
            # （如上一道补充例题的解答尾巴）丢弃，不拼进第一题
            node["exercises"] = split_exercises(
                content,
                keep_leading=not node.get("implicit"),
                # 显式习题节点是已知的习题区域，解答里引用"定理 N"不应触发
                # 小节结尾校验；只有隐式节尾节点（正文中挖题）保留该校验
                check_section_end=node.get("implicit"),
                # 课本普通习题节不默认把“证明：”当作解答；下面按整节
                # 统计候选解答后再决定是否分离。
                split_solutions=False,
            )
            # 隐式节尾习题节点没有对应目录标题，跳过小节过滤，保留该区间内所有编号题
            if not node.get("implicit"):
                node["exercises"] = filter_exercises_by_section(
                    node["exercises"], node["title"]
                )
            # 节尾搜索：剔除 __preamble__（区间内没有编号题），以及 section 归属
            # 是树中其他已知小节的内容（如边界页里下一节的编号定义），
            # 剔除后若没有任何习题则移除该节点
            if node.get("implicit"):
                node["exercises"] = [
                    e
                    for e in node["exercises"]
                    if e["number"] != "__preamble__"
                    and normalize_section(e.get("section")) not in known_sections
                ]
                if node.get("_generic_exercise_heading"):
                    prefix = node["_exercise_heading_number"] + "."
                    node["exercises"] = [e for e in node["exercises"]
                                         if str(e["number"]).startswith(prefix)]
                if not node["exercises"]:
                    return None
                # 区域限制性判断：题号要基本连续才算题目区域
                # （比如某个尾区间里只有零散的编号行，不算习题）
                if not numbers_are_consecutive(
                    [e["number"] for e in node["exercises"]]
                ):
                    return None
            split_solutions_for_section(
                node["exercises"],
                node.get("title") or parent_title,
                doc_type="textbook",
            )
            # 分离完成后，把配图按题干提到的“图N-M”插回对应习题
            attach_figures(node["exercises"], figures)
            # HTML 表格转 markdown 表格（单元格内 LaTeX 渲染问题）
            convert_exercise_tables(node["exercises"])
            # section 只是泛化的“习题/练习题”字样（无编号无知识点）时，
            # 用父级小节名补全，让 section 尽量体现知识点
            fallback = parent_title or node["title"]
            for item in node["exercises"]:
                if is_generic_section(item.get("section")):
                    item["section"] = fallback
                # 课本没有像试卷那样的题型小节，统一按简答题处理，
                # 使三类输入的习题 JSON 至少共享 question_type 字段。
                if item.get("question_type") != "proof":
                    item["question_type"] = "short_answer"
            # 题号统一为按顺序排列的单个数字（1,2,3...），
            # 原题号（可能是多级如 1.1.7）保留在 original_number 供对照
            seq = 0
            for item in node["exercises"]:
                if item["number"] == "__preamble__":
                    continue
                seq += 1
                if item["number"] != str(seq):
                    item["original_number"] = item["number"]
                    item["number"] = str(seq)
                    item["separator"] = "."
            logger.debug(f"[{node['title']}] 提取到 {len(node['exercises'])} 道习题")
        kept_children: list[SectionNode] = []
        for child_node in node.get("children", []):
            child_result = walk(child_node, node["title"])
            if child_result is not None:
                kept_children.append(child_result)
        node["children"] = kept_children
        return node

    sections[:] = [r for s in sections if (r := walk(s, None)) is not None]
