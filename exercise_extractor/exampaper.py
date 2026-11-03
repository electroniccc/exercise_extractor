"""试卷（exampaper）专用提取：整卷 OCR、按题型切分、答案/解析处理。

与 textbook / exercise_book 解耦：

- 不走目录识别流程：整卷（一套试卷）全量 OCR，页码范围 [1, N+1)；
- 按“题型小节”切分：识别“一、选择题”“二、填空题”“三、解答题”这类
  小节（兼容早年“二、（本题满分8分）”这种没有题型词的写法），每道题
  打上 question_type（标准英文枚举，见 QUESTION_TYPES）；
- 英文 A-level 卷按 Section A/B 分组；无 Section 的卷按 Questions 分组，
  识别 Cambridge 独立题号和 AQA ``0 1``/``0 1 . 1`` 题号；
- 题号支持两种形态：连续编号（1. 2. 3. …，近十年真题）与节内
  （1）（2）编号（早年真题的填空题/选择题）；
- 场景处理：
  1. 纯试题：提取题目，answer/solution_steps 为空；
  2. 试题+解析（每道题后跟【答案】/【解】）：把答案与解答过程拆到
     answer / solution_steps；
  3. 试题+文末答案（如“真题+答案”）：检测“参考答案/答案/解析”标题，
     把尾部按题型小节切块，按小节+题号回填到题目。
"""

import logging
import re
from html import unescape
from typing import TypedDict

from .extraction import (
    SectionNode,
    attach_figures,
    convert_exercise_tables,
    extract_figures,
)
from .parse_md_exercises import (
    ANSWER_START_RE,
    Exercise,
    demote_exercise_headings,
    split_at_line_marker,
    _split_solution,
    solution_starts_with_proof,
)

logger = logging.getLogger(__name__)

# 标准化的题型枚举（供其他模块调用，避免中文命名）
QUESTION_TYPES = (
    "single_choice",    # 单选题
    "multiple_choice",  # 多选题
    "fill_in_blank",    # 填空题
    "short_answer",     # 简答题（含解答题/计算题/应用题等）
    "proof",            # 证明题
)

# 题型中文展示名（仅用于输出/调试，数据里存英文枚举）
QUESTION_TYPE_CN = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "fill_in_blank": "填空题",
    "short_answer": "简答题",
    "proof": "证明题",
}

# 题型小节的中文序号：一、二、三…
_CN_SECTION_RE = re.compile(r"^([一二三四五六七八九十]+)\s*[、.．]\s*(.*)$")
_CN_SCORE_NOTE_RE = re.compile(
    r"^\s*[（(]\s*本题\s*(?:满分\s*)?\d+\s*分"
    r"(?:\s*[,，]\s*每小题\s*\d+\s*分)?\s*[）)]"
)
_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.I | re.S)


def _unpack_numbered_question_tables(content: str) -> str:
    """Turn OCR tables with numbered prose rows into ordinary question lines.

    Score grids and numeric data tables stay intact: their second cells do not
    contain a sequence of substantial question text.
    """
    def replace(match: re.Match[str]) -> str:
        items: list[tuple[int, str]] = []
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", match.group(0), re.I | re.S):
            cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, re.I | re.S)
            if len(cells) < 2:
                continue
            number_text = unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
            body = unescape(re.sub(r"<[^>]+>", "", cells[1])).replace(r"\n", "\n").strip()
            if re.fullmatch(r"[1-9]\d?", number_text) and re.search(r"[A-Za-z]{3}", body):
                items.append((int(number_text), body))
        if len(items) < 2 or any(b[0] != a[0] + 1 for a, b in zip(items, items[1:])):
            return match.group(0)
        return "\n\n".join(f"{number} {body}" for number, body in items)

    return _HTML_TABLE_RE.sub(replace, content)
# 数字序号 + 顿号（如 "1、选择题"）也接受，但必须带题型词，避免把题号行当小节
_DIGIT_SECTION_RE = re.compile(r"^(\d{1,2})\s*[、]\s*(.*)$")

# 小节标题里能明确题型的词；没有命中时按“简答题”兜底（早年的
# “二、（本题满分8分）”就是解答题，只是标题没写题型词）
_TYPE_WORDS = (
    "选择题",
    "单项选择题",
    "多项选择题",
    "不定项选择题",
    "单选题",
    "多选题",
    "填空题",
    "解答题",
    "简答题",
    "计算题",
    "证明题",
    "应用题",
    "综合题",
)
_QUESTION_HEADING_START_RE = re.compile(
    r"^(?:已知|设|若|当|给定|对于|求|计算|证明|解|判断|讨论|电路|系统|函数|序列|信号)"
)

# 文末答案区的标题词（“解答题”不算，那是正文题型小节）
_ANSWER_TAIL_WORDS = (
    "参考答案",
    "真题答案",
    "试题答案",
    "答案",
    "试题解析",
    "真题解析",
    "解析",
    "详解",
)

# 解析里内嵌的答案/解答标记：【答案】【解】【解析】【详解】【解法】【分析】【点睛】
# 按“长词优先”排列，避免【解析】被【解】抢先匹配
_MARKER_SPLIT_RE = re.compile(
    r"【\s*(答案|答|解析|详解|解法|分析|点睛|解)\s*】"
)

# 早年填空题/选择题节内用（1）（2）编号：转成 1. 2. 让通用切分器能识别
_PAREN_NUM_RE = re.compile(r"^\s*[（(]\s*(\d{1,3})\s*[）)]\s*", re.MULTILINE)
# 选择题/填空题的题号行：1. / 1、/ 1.C / （1）题目（先经 _PAREN_NUM_RE 转成 1.）
_ITEM_NUM_RE = re.compile(
    r"^\s*[（(]?\s*(\d{1,3})\s*(?:[）)]?\s*(?:\\[.．]|[.．、])|[）)])\s*(.*)$"
)
# 简答题的题号行：17. 内容 / 17、内容（要求点号后有空白，避免把
# “17.2024年…” 里的年份当成多级题号；也避免把行首小数 1.6 当题号）
_SHORT_ANSWER_NUM_RE = re.compile(
    r"^\s*(\d{1,3})\s*(?:\\[.．]|[.．、]|[）)])(?!\d)\s*(.*)$"
)
_DUPLICATE_SOLUTION_RE = re.compile(
    r"^\s*(?:"
    r"【\s*(?:答案|答|解析|详解|解法|分析|点睛|解)\s*】"
    r"|(?:解答|解析|详解|分析|点睛|证明|证|解|答)\s*[：:.．]?"
    r")"
)
_CHOICE_MARKER_PAREN_RE = re.compile(
    r"(?<![A-Za-z])[（(]([ABCD])[）)]\s*[.．]?"
)
_CHOICE_MARKER_ALT_RE = re.compile(
    r"(?<![A-Za-z])\b([ABCD])[)）、：:]\s*"
)


def _normalize_choice_markers(content: str) -> str:
    """统一选择题选项标记为 ``A. 内容`` 形式。"""
    content = _CHOICE_MARKER_PAREN_RE.sub(r"\1. ", content)
    return _CHOICE_MARKER_ALT_RE.sub(r"\1. ", content)


def _repair_truncated_numbers(exercises: list[Exercise]) -> None:
    """修复填空/选择题中题号首位被 OCR 吞掉的连续编号。"""
    previous: int | None = None
    for item in exercises:
        try:
            number = int(str(item.get("number", "")))
        except ValueError:
            previous = None
            continue
        if previous is not None and number < 10 and number <= previous and previous >= 10:
            candidate = previous // 10 * 10 + number
            if candidate > previous:
                item["number"] = str(candidate)
                number = candidate
        previous = number
# OCR 可能把整份试卷的题号行都渲染成标题（“### 18. （本小题满分…）”），
# 通用“标题还原”规则看到下一个标题就停止扫描，连续标题时还原不了。
# 试卷题型小节是已知的题目区域，直接把“# + 数字 + 分隔符”降级为普通题号行。
_HEADING_PREFIX_RE = re.compile(r"(?m)^#{1,6}\s*")
_EN_SECTION_RE = re.compile(r"^\s*(?:#{1,6}\s*)?Section\s+([A-Z])\s*$", re.I)
_EN_PAGE_CODE_RE = re.compile(
    r"^(?:\d{4}/\d{1,2}/[A-Z]/[A-Z]/\d{2}|IB/M/\w+|"
    r"\d{4}/\d{1,2}/\d{2}/[A-Z]\d{2}|CLES\s+\d{4}|"
    r"(?:[A-Z]/)?(?:Jun|May|Nov)\d{2}/\d{4}/\d|©\s*UCLES)", re.I
)
_EN_FURNITURE_RE = re.compile(
    r"^(?:Do not write(?: outside the box)?|outside the(?:\s+box)?|box|"
    r"Turn over(?: for the next question)?|\[Turn over\]|\[Unabel\]|"
    r"DO NOT WRITE ON THIS PAGE ANSWER IN THE SPACES PROVIDED|"
    r"20\d{2}|二〇二四年|\(\d{2}\)|\*\d+\*|"
    r"There are no questions printed on this page)\s*$",
    re.I,
)
_EN_SPACED_NUMBER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?([0-3])\s+([0-9])(?:\s*\.\s*(\d+))?\s*(.*)$"
)
_EN_NUMBER_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(\d{1,2})(?:[.)](?!\d))?(?=\s|$)\s*(.*)$"
)


class TypeSection(TypedDict, total=False):
    """一道试卷里的题型小节（如“一、选择题”）。"""

    index: int          # 小节在文档里的顺序（1 起）
    title: str          # 小节标题全文（去 #，如“一、选择题：1-10 题”）
    section_key: str    # 归一化小节键（如“一、选择题”/“二”），用于答案回填
    question_type: str  # 标准英文枚举（single_choice/multiple_choice/…）
    start_page: int | None  # 题型标题所在页（页标记在标题前时保留）
    lines: list[str]    # 小节正文（不含标题行）
    english_paper: bool  # 英文 A-level 卷面采用独立的顶层题号切分


def _looks_english_paper(content: str) -> bool:
    """识别英文试卷封面、分节或无封面的连续英文题干。"""
    visible = re.sub(r"<[^>]+>", "", content)
    if (
        any(_EN_SECTION_RE.match(line) for line in visible.splitlines())
        or re.search(r"(?i)\bA[ -]level\b", visible)
        or re.search(r"(?i)Cambridge International AS\s*&\s*A Level", visible)
    ):
        return True
    # 截取页可能没有封面；模型还会把页码和考试代码都省掉。只看可见
    # 的英文题干，避免绝对图片路径里的 ``a-level`` 意外触发识别。
    if re.search(r"[\u4e00-\u9fff]", visible):
        return False
    prose_lines = sum(
        bool(re.search(r"\b(?:The|Express|Hence|Find|Calculate|Determine|Describe)\b",
                       line, re.I))
        for line in visible.splitlines()
    )
    return prose_lines >= 3 and any(
        _english_question_start(line) for line in visible.splitlines()
    )


def _split_english_type_sections(content: str) -> list[TypeSection]:
    """按 Section A/B 分块；无 Section 的 Cambridge 卷合成 Questions。"""
    sections: list[TypeSection] = []
    current: TypeSection | None = None
    leading: list[str] = []
    current_page: int | None = None

    for line in content.split("\n"):
        page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if page:
            current_page = int(page.group(1))
            if current is not None:
                current["lines"].append(line)
            else:
                leading.append(line)
            continue
        heading = _EN_SECTION_RE.match(line)
        if heading:
            if current is not None:
                sections.append(current)
            title = f"Section {heading.group(1).upper()}"
            current = {
                "index": len(sections) + 1,
                "title": title,
                "section_key": title,
                "question_type": "short_answer",
                "start_page": current_page,
                "english_paper": True,
                "lines": [],
            }
            continue
        if current is None:
            leading.append(line)
        else:
            current["lines"].append(line)
    if current is not None:
        sections.append(current)
    if not sections:
        sections.append({
            "index": 1,
            "title": "Questions",
            "section_key": "Questions",
            "question_type": "short_answer",
            "start_page": None,
            "english_paper": True,
            "lines": leading,
        })
    for section in sections:
        if re.search(r"(?i)(?:four responses|select the best response|multiple.choice)",
                     "\n".join(section["lines"][:20])):
            section["question_type"] = "single_choice"
    return sections


def _strip_heading(line: str) -> str:
    """去掉 markdown 标题前缀。"""
    return re.sub(r"^#+\s*", "", line).strip()


def _classify_type(text: str) -> str:
    """按标题文字判断题型，返回标准英文枚举。

    没有明确题型词时按 short_answer 兜底（早年的“二、（本题满分8分）”
    就是解答题，只是标题没写题型词）。普通“选择题”默认按单选题处理，
    明确写“多选/单选”时按对应类型。
    """
    if any(k in text for k in ("多选", "多项选择", "不定项")):
        return "multiple_choice"
    if "单选" in text:
        return "single_choice"
    if "选择" in text:
        return "single_choice"
    if "填空" in text:
        return "fill_in_blank"
    if _chinese_type_word(text) == "证明题":
        return "proof"
    return "short_answer"


def _chinese_type_word(text: str) -> str | None:
    """从标题里提取中文题型词（选择题/填空题/证明题…），用于 section_key。"""
    for word in (
        "多项选择题",
        "单项选择题",
        "不定项选择题",
        "单选题",
        "多选题",
        "选择题",
        "填空题",
        "证明题",
        "解答题",
        "简答题",
        "计算题",
        "应用题",
        "综合题",
    ):
        if word in text:
            return word
    return None


def _is_section_start_line(line: str) -> bool:
    """判断一行是否是题型小节标题（兼容 # 标题与裸标题行）。

    - 中文序号（一、二、…）后带题型词 / “满分” / “本题共” → 是；
    - 数字 + 顿号（1、选择题）→ 必须带题型词；
    - 其余行不算，避免把题号/正文误判成小节。
    """
    text = _strip_heading(line)
    if not text:
        return False
    m = _CN_SECTION_RE.match(text)
    if m:
        rest = m.group(2)
        if any(w in rest for w in _TYPE_WORDS):
            return True
        if _CN_SCORE_NOTE_RE.match(rest):
            # 旧竞赛卷常用“二、（本题15分）设…”直接标记一道大题，
            # 其中分值说明把真正的题干起始词隔开了。
            return True
        if "满分" in rest or "本题共" in rest:
            return True
        if any(marker in rest for marker in ("【解】", "【答案】", "【解析】")):
            # 解析文档里“二、【解】”这类无题型词、无分值的节标题
            return True
        # 有些试卷把每道大题直接写成“二、已知…/三、求…”；这不是题型
        # 小节，但仍是可靠的顶层题目边界，不能把标题内容丢掉。
        if _QUESTION_HEADING_START_RE.match(rest.strip()):
            return True
        return False
    m = _DIGIT_SECTION_RE.match(text)
    if m:
        rest = m.group(2)
        return any(w in rest for w in _TYPE_WORDS)
    return False


def _is_answer_tail_heading(line: str) -> bool:
    """判断一行是否是文末答案区的标题（参考答案/真题答案/试题解析…）。"""
    # 解析文档里每题后的【答案】【解】是内嵌标记，不是文末答案区
    if "【答案】" in line or "【解】" in line or "【解析】" in line:
        return False
    text = _strip_heading(line)
    if not text:
        return False
    if _is_section_start_line(text):
        # 题型小节标题（如“二、填空题：…请将答案写在答题纸…”）不是答案区；
        # 只有“题型词+答案/解析”紧邻（如“一、选择题答案”）才是答案区小节
        return bool(
            re.search(
                r"(选择题|单项选择题|多项选择题|不定项选择题|单选题|多选题|"
                r"填空题|解答题|简答题|计算题|证明题|应用题|综合题)"
                r"\s*(答案|解析|详解)",
                text,
            )
        )
    if line.lstrip().startswith("#"):
        return any(w in text for w in _ANSWER_TAIL_WORDS)
    # 裸标题行：只认“参考答案/答案/解析/详解”这类独立标题形态
    return bool(
        re.match(
            r"^[一二三四五六七八九十]*\s*[、.．]?\s*"
            r"(参考答案|试题答案|真题答案|答案|试题解析|真题解析|解析|详解)",
            text,
        )
    )


def _is_answer_tail_table(line: str) -> bool:
    """判断一行是否是答案区的“题号/答案”表格（无标题时靠表格头识别）。"""
    if "<table" not in line.lower():
        return False
    text = re.sub(r"<[^>]+>", "", line)
    return "题号" in text and "答案" in text


def _section_key(text: str) -> str:
    """从小节标题提取归一化键：“一、选择题：1-10 题” → “一、选择题”。

    早年“二、（本题满分8分）”没有题型词，键就是中文序号“二”；
    文末“一、选择题答案”也能归到“一、选择题”。section_key 保留中文，
    与 question_type（英文枚举）解耦，便于阅读和跨文档匹配。
    """
    m = _CN_SECTION_RE.match(text)
    numeral = m.group(1) if m else ""
    rest = m.group(2) if m else text
    type_word = _chinese_type_word(rest)
    if numeral:
        return f"{numeral}、{type_word}" if type_word else numeral
    return type_word or "简答题"


def split_type_sections(
    content: str, in_tail: bool = False
) -> tuple[list[TypeSection], str | None]:
    """把整卷内容切成题型小节，并识别文末答案区。

    返回 (sections, tail_content)：
    - sections：正文题型小节（选择题/填空题/简答题），不含答案区；
    - tail_content：文末答案区原文（没有则为 None）。
    答案区标题只在“已经出现过至少一个题型小节”后才生效，
    避免把封面标题“2024考研数学一真题+答案”误当成答案区。

    in_tail=True 时不再识别答案区边界：答案区里“## 二、填空题答案”这类
    小节标题虽然含“答案”，但只是答案区自己的题型小节，不是新的答案区。
    """
    if not in_tail and _looks_english_paper(content):
        return _split_english_type_sections(content), None
    sections: list[TypeSection] = []
    tail_lines: list[str] | None = None
    current: TypeSection | None = None
    index = 0
    seen_question_section = False
    current_page: int | None = None
    leading_lines: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is not None:
            # 保留 OCR 原文中的空行。选择题选项通常依靠题目与 A/B/C/D
            # 之间的空行分隔；过早删除会使 Markdown 预览把所有选项揉成一段。
            # 外层切分器会自行 trim 首尾空白，不会把这些空行带到结果边界。
            if any(l.strip() for l in current["lines"]):
                sections.append(current)
        current = None

    for line in content.split("\n"):
        if tail_lines is not None:
            tail_lines.append(line)
            continue
        m_page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m_page:
            current_page = int(m_page.group(1))
            # 小节已经开始时保留页标记，切分器才能更新跨页题目的结束页；
            # 小节尚未开始时由 start_page 接住，避免题型标题前的页标记丢失。
            if current is not None:
                current["lines"].append(line)
            continue
        if (
            not in_tail
            and seen_question_section
            and (
                _is_answer_tail_heading(line)
                or _is_answer_tail_table(line)
            )
        ):
            flush()
            tail_lines = [line]
            continue
        if _is_section_start_line(line):
            if current is None and leading_lines:
                # 第一大题有时没有“一、…”标题，直接从（1）开始；遇到
                # “二、已知…”时，把前置括号题作为隐含的第一题型小节。
                if _PAREN_NUM_RE.search("\n".join(leading_lines)):
                    index += 1
                    current = {
                        "index": index,
                        "title": "一、简答题",
                        "section_key": "一、简答题",
                        "question_type": "short_answer",
                        "start_page": None,
                        "lines": leading_lines,
                        "parenthesized_top_level": True,
                    }
                    flush()
                leading_lines = []
            flush()
            index += 1
            title = _strip_heading(line)
            # 解析文档里“二、【解】由极限得…”这类标题：标记随标题走，
            # 把【解】/【答案】摘出来作为小节正文的首行，让后续切分能识别
            m_marker = re.search(r"【(?:解|解析|详解|解法|答案)】", title)
            if m_marker:
                marker = m_marker.group(0)
                rest = title[m_marker.end():].strip()
                title = title[:m_marker.start()].strip()
                current = {
                    "index": index,
                    "title": title,
                    "section_key": _section_key(title),
                    "question_type": _classify_type(title),
                    "start_page": current_page,
                    "lines": [marker],
                }
                if rest:
                    current["lines"].append(rest)
                seen_question_section = True
                continue
            current = {
                "index": index,
                "title": title,
                "section_key": _section_key(title),
                "question_type": _classify_type(title),
                "start_page": current_page,
                "lines": [],
            }
            if (
                not _chinese_type_word(title)
                and "满分" not in title
                and "本题共" not in title
            ):
                section_match = _CN_SECTION_RE.match(title)
                if section_match:
                    current["lines"].append(section_match.group(2).strip())
            seen_question_section = True
            continue
        if current is not None:
            current["lines"].append(line)
        else:
            leading_lines.append(line)
    flush()

    tail = "\n".join(tail_lines).strip() if tail_lines is not None else None
    return sections, tail


def _parenthesized_numbers_to_plain(content: str) -> str:
    """把行首 （1）（2） 编号转成 1. 2.，供填空题/选择题节内切分。"""
    return _PAREN_NUM_RE.sub(lambda m: f"{m.group(1)}. ", content)


def _uses_parenthesized_numbering(chunk: str) -> bool:
    """判断该节是否用（1）（2）括号编号（早年试卷）。

    跳过开头非编号行（小节标题可能换行延续到正文里），
    看第一个编号行是括号式还是点号式。
    """
    for line in chunk.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _PAREN_NUM_RE.match(stripped):
            return True
        if _ITEM_NUM_RE.match(stripped):
            return False
    return False


def _declares_multiple_subquestions(title: str) -> bool:
    """题型标题明确声明多道小题时，括号编号应拆成独立习题。"""
    match = re.search(
        r"(?:本题\s*共\s*|共\s*)(\d+)\s*(?:道)?\s*(?:小题|题)",
        title,
    )
    if match:
        return int(match.group(1)) > 1
    scores = re.search(
        r"本题\s*(?:满分\s*)?(\d+)\s*分\s*[,，]\s*"
        r"每小题\s*(\d+)\s*分",
        title,
    )
    return bool(scores and int(scores.group(2)) > 0
                and int(scores.group(1)) > int(scores.group(2)))


def _strip_scoring_marks(content: str) -> str:
    """去掉试卷的空评分表及 OCR 拆出的独立评分栏文字。"""
    def replace(match: re.Match[str]) -> str:
        rows = [
            [
                re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cell))
                for cell in re.findall(
                    r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, re.I | re.S
                )
            ]
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", match.group(), re.I | re.S)
        ]
        return "" if rows == [["得分", ""], ["评阅人", ""]] else match.group()

    content = _HTML_TABLE_RE.sub(replace, content)
    return re.sub(r"(?m)^[ \t]*(?:得分|评阅人)[ \t]*$", "", content)


def _parenthesized_question_numbers_to_plain(content: str) -> str:
    """简答题小节里把 >=10 的括号题号（如 (17)）转成 17.。

    (1)(2) 通常是同一道解答题的子问（“（1）求 y(x);（2）求最大值.”），
    不能转；而解析文档的解答题题号是跨卷连续的 (17)~(22)，必须转。
    """
    def repl(m: re.Match[str]) -> str:
        number = int(m.group(1))
        return f"{number}. " if number >= 10 else m.group(0)

    return _PAREN_NUM_RE.sub(repl, content)


def _section_pages(lines: list[str]) -> list[int] | None:
    """从小节正文里的页间标记提取页码范围。"""
    pages = []
    for line in lines:
        m = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m:
            pages.append(int(m.group(1)))
    if not pages:
        return None
    return [pages[0], pages[-1]]


def _split_simple_numbered_exercises(
    chunk: str, start_page: int | None = None
) -> list[Exercise]:
    """按行切分选择题/填空题（题号逐行出现，不用通用多级题号正则）。

    通用切分器会把 “11. 3” 这类答案行误判成多级题号 11.3；
    选择题/填空题的题号始终是单级数字，按行首题号直接切更稳。
    """
    items: list[Exercise] = []
    current: Exercise | None = None
    current_page: int | None = start_page
    start_page: int | None = None
    end_page: int | None = None
    for line in chunk.split("\n"):
        m_page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m_page:
            current_page = int(m_page.group(1))
            continue
        m = _ITEM_NUM_RE.match(line)
        if m:
            if (
                current is not None
                and current["number"] == m.group(1)
                and _DUPLICATE_SOLUTION_RE.match(m.group(2))
            ):
                # OCR 有时把题号再次写在“解：”前（如 ``17\.题干``、
                # 下一行 ``17. 解：``）。这是同一道题的解析，不应拆成
                # 两道同号习题。
                current["content"] += "\n" + m.group(2)
                end_page = current_page
                continue
            if current is not None:
                current["content"] = current["content"].strip()
                if start_page is not None and end_page is not None:
                    current["pages"] = [start_page, end_page]
                items.append(current)
            current = {
                "number": m.group(1),
                "separator": ".",
                "content": m.group(2).strip(),
            }
            start_page = current_page
            end_page = current_page
            continue
        if current is not None:
            # 不要在每行追加时调用 strip：OCR 用空行分隔选择题选项，
            # 逐行 strip 会把这些 Markdown 段落边界提前吃掉。
            current["content"] += "\n" + line
            end_page = current_page
    if current is not None:
        current["content"] = current["content"].strip()
        if start_page is not None and end_page is not None:
            current["pages"] = [start_page, end_page]
        items.append(current)
    return items


def _split_short_answer_exercises(
    chunk: str, start_page: int | None = None
) -> list[Exercise]:
    """按行切分简答题（题号行 + 后续行），保留 (1)(2) 子问不拆。

    通用切分器会把 “17. 2024年…” 误判成多级题号 17.2024（年份粘连）；
    简答题题号是单级数字，按行首题号直接切。子问 (1)(2) 不带“数字+点号”
    形态，会作为当前题的正文保留。
    """
    items: list[Exercise] = []
    current: Exercise | None = None
    current_page: int | None = start_page
    start_page: int | None = None
    end_page: int | None = None
    for line in chunk.split("\n"):
        m_page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m_page:
            current_page = int(m_page.group(1))
            continue
        m = _SHORT_ANSWER_NUM_RE.match(line)
        if m:
            if (
                current is not None
                and current["number"] == m.group(1)
                and _DUPLICATE_SOLUTION_RE.match(m.group(2))
            ):
                current["content"] += "\n" + m.group(2)
                end_page = current_page
                continue
            if current is not None:
                current["content"] = current["content"].strip()
                if start_page is not None and end_page is not None:
                    current["pages"] = [start_page, end_page]
                items.append(current)
            current = {
                "number": m.group(1),
                "separator": ".",
                "content": m.group(2).strip(),
            }
            start_page = current_page
            end_page = current_page
            continue
        if current is not None:
            current["content"] += "\n" + line
            end_page = current_page
    if current is not None:
        current["content"] = current["content"].strip()
        if start_page is not None and end_page is not None:
            current["pages"] = [start_page, end_page]
        items.append(current)
    return items


def _english_question_start(line: str) -> tuple[int, str] | None:
    """识别 Cambridge 顶层题号与 AQA 的 ``0 1``，排除 ``01.2`` 小问。"""
    spaced = _EN_SPACED_NUMBER_RE.match(line)
    if spaced:
        number = int(spaced.group(1) + spaced.group(2))
        if spaced.group(3) is not None:
            # AQA 有时省去独立的 ``0 3``，直接以 ``0 3 . 1`` 开题。
            return (number, line.strip()) if spaced.group(3) == "1" else None
        if 1 <= number <= 99:
            return number, spaced.group(4).strip()
    plain = _EN_NUMBER_RE.match(line)
    if plain:
        number = int(plain.group(1))
        body = plain.group(2).strip()
        if 1 <= number <= 99 and not re.match(r"(?i)^hours?\b", body):
            return number, body
    return None


def _english_page_starts_unnumbered_question(content: str) -> bool:
    """判断漏题号的页是否从新的英文题干开始，而非 (b) 等跨页小问。"""
    visible = re.sub(r"<[^>]+>", "", content).strip()
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if not lines or re.match(r"^\([a-z]\)", lines[0], re.I):
        return False
    words = re.findall(r"[A-Za-z]{3,}", visible)
    return len(words) >= 8 and bool(re.match(
        r"(?i)^(?:The|In|A|An|Find|Calculate|Determine|Describe|"
        r"Show|Prove|Express)\b",
        lines[0],
    ))


def _split_english_numbered_exercises(section: TypeSection) -> list[Exercise]:
    """英文卷面按递增顶层题号切分，保留字母/小数小问。"""
    items: list[Exercise] = []
    current: Exercise | None = None
    page = section.get("start_page")
    start_page: int | None = None
    end_page: int | None = None
    previous: int | None = None
    line_on_page = 0
    page_number_skipped = False
    blank_page = False
    page_breaks: list[tuple[int, int]] = []
    lines = section["lines"]

    def flush() -> None:
        if current is None:
            return
        current["content"] = current["content"].strip()
        if start_page is not None and end_page is not None:
            current["pages"] = [start_page, end_page]
        items.append(current)

    for index, line in enumerate(lines):
        page_match = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if page_match:
            page = int(page_match.group(1))
            if current is not None:
                page_breaks.append((page, len(current["content"])))
            line_on_page = 0
            page_number_skipped = False
            blank_page = False
            continue
        line_on_page += 1
        stripped = _strip_heading(line)
        if stripped.lower() in (
            "there are no questions printed on this page", "blank page"
        ):
            blank_page = True
        if blank_page:
            continue
        if re.match(r"(?i)^(?:Additional page|Permission to reproduce)\b", stripped):
            break
        following = next(
            (_strip_heading(next_line) for next_line in lines[index + 1:index + 7]
             if _strip_heading(next_line)),
            "",
        )
        if (
            page is not None and line_on_page <= 2
            and not page_number_skipped and stripped == str(page)
        ):
            # OCR may omit the printed page number and leave only a question
            # with the same number at the start of that page.
            likely_question = (
                previous == page - 1
                and bool(re.match(
                    r"(?i)^(?:The|Find|Given|Show|A|An|Determine|Calculate|Express)\b",
                    following,
                ))
            )
            if not likely_question:
                page_number_skipped = True
                continue
        if _EN_FURNITURE_RE.match(stripped) or _EN_PAGE_CODE_RE.match(stripped) or (
            stripped.isdigit() and _EN_PAGE_CODE_RE.match(following)
        ):
            continue
        candidate = _english_question_start(line)
        if candidate:
            number, body = candidate
            if body and not re.search(r"[A-Za-z]{2,}", body) and re.fullmatch(
                r"[-+−=÷×/ ]+", body
            ):
                candidate = None
            # AQA 的选项可能只是独立一行数字（如 ``2``）。真正的裸题号
            # 后面通常接题干文字或图片，数字选项后面则仍是数值。
            if candidate and not body and not (
                re.search(r"[A-Za-z]{2,}", following)
                or following.startswith("<img")
                or (line_on_page <= 8 and (current is None or end_page != page))
            ):
                candidate = None
        if candidate:
            number, body = candidate
            allowed_first = section["title"] != "Questions" or number == 1
            gap = number - previous if previous is not None else 0
            missing_pages: list[tuple[int, int]] = []
            if current is not None and 2 <= gap <= 4 and page is not None:
                boundaries = [
                    (start_page, 0), *page_breaks,
                    (page + 1, len(current["content"])),
                ]
                for (break_page, offset), (_, next_offset) in zip(
                    boundaries, boundaries[1:]
                ):
                    if (
                        break_page is not None
                        and start_page is not None
                        and start_page < break_page < page
                        and _english_page_starts_unnumbered_question(
                            current["content"][offset:next_offset]
                        )
                    ):
                        missing_pages.append((break_page, offset))
            gap_recovered = gap >= 2 and len(missing_pages) == gap - 1
            if (previous is None and allowed_first) or (
                previous is not None and (gap == 1 or gap_recovered or gap == 2)
            ):
                if gap_recovered and current is not None and previous is not None:
                    old_content = current["content"]
                    starts = [(start_page, 0), *missing_pages]
                    page_bounds = [*starts, (page, len(old_content))]
                    for offset_index, (segment_page, segment_offset) in enumerate(starts):
                        next_page, next_offset = page_bounds[offset_index + 1]
                        current = {
                            "number": str(previous + offset_index),
                            "separator": ".",
                            "content": old_content[segment_offset:next_offset],
                        }
                        start_page = segment_page
                        end_page = segment_page
                        for (part_page, part_offset), (_, part_end) in zip(
                            boundaries, boundaries[1:]
                        ):
                            if (
                                part_page is not None
                                and segment_page is not None
                                and segment_page <= part_page < next_page
                                and old_content[part_offset:part_end].strip()
                            ):
                                end_page = part_page
                        flush()
                else:
                    flush()
                current = {"number": str(number), "separator": ".", "content": body}
                previous = number
                start_page = page
                end_page = page
                page_breaks = []
                continue
        if current is not None:
            current["content"] += "\n" + line
            if stripped:
                end_page = page
    flush()
    return items


def _split_section_exercises(section: TypeSection) -> list[Exercise]:
    """把一个题型小节切成习题，并统一补上 question_type/section 字段。

    - 选择题/填空题：节内（1）（2）编号转成 1. 2.；
    - 简答题：只转 >=10 的括号题号（如 (17)，解析文档的连续题号；
      (1)(2) 子问不转），整节没有编号时作为一道题兜底
      （早年“二、（本题满分8分）”就是单题小节）。
    """
    chunk = "\n".join(section["lines"]).strip()
    if not chunk:
        return []
    if section.get("english_paper"):
        chunk = _unpack_numbered_question_tables(chunk)
    if not section.get("english_paper"):
        chunk = _strip_scoring_marks(chunk)
    chunk = demote_exercise_headings(chunk)
    chunk = _HEADING_PREFIX_RE.sub("", chunk)
    # A-level 图表往往没有中文式图注。保留图片所在行，按页和
    # 题号直接归入题干；通用图注抽取会丢弃无图注的图片。
    figures: dict[str, list[str]] = {}
    if not section.get("english_paper"):
        chunk, figures = extract_figures(chunk)
    if section.get("english_paper"):
        english_section = dict(section)
        english_section["lines"] = chunk.split("\n")
        exercises = _split_english_numbered_exercises(english_section)
    elif section.get("parenthesized_top_level"):
        chunk = _parenthesized_numbers_to_plain(chunk)
        exercises = _split_short_answer_exercises(
            chunk, section.get("start_page")
        )
    elif section["question_type"] in (
        "single_choice",
        "multiple_choice",
        "fill_in_blank",
    ):
        # 只有该节第一道题就是（1）（2）括号编号（早年试卷）时才整体转换；
        # 现代试卷第一道题是 “1.”/“13.”，节内的（1）（2）是子问，不能转
        if _uses_parenthesized_numbering(chunk):
            chunk = _parenthesized_numbers_to_plain(chunk)
        exercises = _split_simple_numbered_exercises(
            chunk, section.get("start_page")
        )
        _repair_truncated_numbers(exercises)
    else:
        if (
            _declares_multiple_subquestions(section["title"])
            and _uses_parenthesized_numbering(chunk)
        ):
            chunk = _parenthesized_numbers_to_plain(chunk)
        else:
            chunk = _parenthesized_question_numbers_to_plain(chunk)
        exercises = _split_short_answer_exercises(
            chunk, section.get("start_page")
        )
    if not exercises and not section.get("english_paper"):
        pages = _section_pages(section["lines"])
        if not pages and section.get("start_page") is not None:
            pages = [section["start_page"], section["start_page"]]
        item: Exercise = {
            "number": "1",
            "separator": ".",
            "content": chunk,
        }
        if pages:
            item["pages"] = pages
        exercises = [item]
    # 题行内的“答：/解：/证明：”拆到 answer/solution_steps（与通用切分器一致）
    proof_solution_flags: list[bool] = []
    for item in exercises:
        proof_solution = solution_starts_with_proof(item.get("content", ""))
        proof_solution_flags.append(proof_solution)
        content, answer = split_at_line_marker(
            item.get("content", ""), ANSWER_START_RE
        )
        content, solution = _split_solution(content)
        item["content"] = content
        item["answer"] = answer or ""
        item["solution_steps"] = solution or ""
    attach_figures(exercises, figures)
    convert_exercise_tables(exercises)
    for item, proof_solution in zip(exercises, proof_solution_flags):
        item["question_type"] = section["question_type"]
        if section.get("english_paper") and re.search(
            r"(?i)\bCircle your answer\b", item.get("content", "")
        ):
            item["question_type"] = "single_choice"
        if (
            section["question_type"] == "short_answer"
            and proof_solution
            and item.get("solution_steps")
        ):
            item["question_type"] = "proof"
        item["section"] = section["title"]
        item["section_key"] = section["section_key"]
        item.setdefault("answer", "")
        item.setdefault("solution_steps", "")
    return exercises


def split_inline_answers(exercises: list[Exercise]) -> None:
    """把解析文档里内嵌的【答案】/【解】/【详解】/【分析】拆到对应字段。

    【答案】/【答】→ answer，其余解析类标记 → solution_steps。
    标记可能独占一行（“【解】由…”），也可能跟在答案后面
    （“(0,+∞) 【分析】由…”），这里按任意位置切分。
    行首“答：/解：”已由通用切分器处理，这里不重复。
    """
    for item in exercises:
        content = item.get("content", "")
        if not content:
            continue
        bucket = "question"
        question_lines: list[str] = []
        answer_lines: list[str] = []
        solution_lines: list[str] = []
        parts = _MARKER_SPLIT_RE.split(content)
        for i, part in enumerate(parts):
            if i % 2 == 1:
                # 标记段（【答案】【详解】…），决定后续文本归属
                bucket = "answer" if part in ("答案", "答") else "solution"
                continue
            text = part.strip()
            if not text:
                continue
            if bucket == "question":
                question_lines.append(text)
            elif bucket == "answer":
                answer_lines.append(text)
            else:
                solution_lines.append(text)
        item["content"] = "\n".join(question_lines).strip()
        answer = "\n".join(answer_lines).strip()
        solution = "\n".join(solution_lines).strip()
        if answer:
            item["answer"] = answer
        if solution:
            item["solution_steps"] = solution


def _split_answer_tail(tail_content: str) -> list[TypeSection]:
    """把文末答案区按题型小节切块（答案区里可能没有“一、”标题）。"""
    sections, _ = split_type_sections(tail_content, in_tail=True)
    return sections


_INLINE_ANSWER_LABEL_RE = re.compile(
    r"^\s*\*\*\s*(?:解|答案|解析|详解)\s*\*\*\s*[：:]?\s*"
)


def _numbered_answer_line(line: str) -> tuple[str, str] | None:
    """识别答案行开头的题号，兼容答案标记、Markdown 斜体和公式行。"""
    text = _INLINE_ANSWER_LABEL_RE.sub("", line, count=1)
    math_match = re.match(r"^(\s*\$\$.*?&)\s*[（(]\s*(\d{1,3})\s*[）)](.*)$", text)
    if math_match:
        number = math_match.group(2)
        return number, math_match.group(1) + math_match.group(3)
    patterns = (
        r"^\s*[（(]\s*(\d{1,3})\s*[）)]\s*(.*)$",
        r"^\s*\*(\d{1,3})\s*[.．、]\*\s*(.*)$",
        r"^\s*(\d{1,3})\s*[.．、]\s*(.*)$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1), match.group(2)
    return None


def _normalize_answer_numbering(
    block: TypeSection, expected_numbers: list[str]
) -> TypeSection | None:
    """仅当答案编号与目标题号完整一致时，把特殊答案标签转成行首题号。"""
    if len(expected_numbers) < 2:
        return None
    normalized_lines: list[str] = []
    observed_numbers: list[str] = []
    for line in block.get("lines", []):
        parsed = _numbered_answer_line(line)
        if parsed:
            number, content = parsed
            observed_numbers.append(number)
            normalized_lines.append(f"{number}. {content}".rstrip())
        else:
            normalized_lines.append(line)
    if observed_numbers != expected_numbers:
        return None
    return {**block, "lines": normalized_lines}


def _match_tail_answers(
    questions: list[Exercise], tail_sections: list[TypeSection]
) -> None:
    """把文末答案按“小节键 + 题号”回填到题目。

    优先用 section_key（如“一、选择题”）精确匹配；匹配不到时退化为
    “题型 + 题号”全卷匹配，兜底答案区缺少部分小节标题的情况。
    """
    by_key: dict[str, list[Exercise]] = {}
    for q in questions:
        by_key.setdefault(q.get("section_key"), []).append(q)

    def fill_question(q: Exercise, a: Exercise) -> None:
        if a.get("answer") and not q.get("answer"):
            q["answer"] = a["answer"]
        if a.get("solution_steps") and not q.get("solution_steps"):
            q["solution_steps"] = a["solution_steps"]
        if not q.get("answer") and a.get("content"):
            if q.get("question_type") in ("short_answer", "proof"):
                if a.get("solution_steps"):
                    # 形如 “17. (1) ω=1 【详解】由…”：标记前是直接答案
                    q["answer"] = a["content"]
                else:
                    # 只有整段解答（无解析标记）：放 solution_steps 更合适
                    q["solution_steps"] = q.get("solution_steps") or a["content"]
            else:
                # 选择题/填空题：答案就是正文内容（如 “C”、“3”）
                q["answer"] = a["content"]

    def answer_pool(block: TypeSection) -> list[Exercise]:
        key = block.get("section_key")
        pool = by_key.get(key, []) if key else []
        if not pool:
            # 退化：按题型匹配（旧试卷可能出现答案区只列了部分小节）
            if block.get("question_type"):
                pool = [
                    q
                    for q in questions
                    if q.get("question_type") == block.get("question_type")
                ]
            else:
                # 答案区连小节标题都没有（如“题号/答案”表格直接开头）：
                # 按题号全卷匹配
                pool = questions
        return pool

    def match_in(block: TypeSection, pool: list[Exercise]) -> None:
        for a in block.get("answers", []):
            number = a.get("number")
            for q in pool:
                if q.get("number") == number:
                    fill_question(q, a)
                    break

    for block in tail_sections:
        pool = answer_pool(block)
        answers = _split_section_exercises(block)
        expected_numbers = [str(question.get("number", "")) for question in pool]
        actual_numbers = [str(answer.get("number", "")) for answer in answers]
        if actual_numbers != expected_numbers:
            normalized = _normalize_answer_numbering(block, expected_numbers)
            if normalized is not None:
                answers = _split_section_exercises(normalized)
        block["answers"] = answers
        # 答案区里可能有【解】/【答案】标记，先拆到 answer/solution_steps
        split_inline_answers(block["answers"])
        match_in(block, pool)


def extract_exercises(sections: list[SectionNode]) -> None:
    """试卷版提取入口：整卷切分题型小节并生成独立 section 子节点。"""
    for node in sections:
        if not node.get("exampaper"):
            continue
        content = node.get("exercise_content", "")
        type_sections, tail_content = split_type_sections(content)
        exercises: list[Exercise] = []
        for section in type_sections:
            exercises.extend(_split_section_exercises(section))
        review_source_by_key = {
            str(section.get("section_key") or "简答题"): "\n".join(
                section.get("lines", [])
            ).strip()
            for section in type_sections
        }
        tail_sections_for_review = (
            _split_answer_tail(tail_content) if tail_content else []
        )
        for key in review_source_by_key:
            matching_tail = [
                section
                for section in tail_sections_for_review
                if section.get("section_key") == key
            ]
            if matching_tail:
                answer_markdown = "\n\n".join(
                    "\n".join(section.get("lines", [])) for section in matching_tail
                ).strip()
            elif tail_content and (
                not tail_sections_for_review
                or any(
                    not section.get("section_key")
                    for section in tail_sections_for_review
                )
            ):
                answer_markdown = tail_content.strip()
            else:
                answer_markdown = ""
            if answer_markdown:
                question_markdown = review_source_by_key[key]
                review_source_by_key[key] = "\n\n".join(
                    part for part in (question_markdown, answer_markdown) if part
                )
        # 内嵌解析：【答案】/【解】拆到 answer / solution_steps
        split_inline_answers(exercises)
        # 内嵌答案已拆出后，再只规范选择题题干中的选项标记，避免把答案
        # ``（C）`` 误改成题干选项。
        for item in exercises:
            if item.get("question_type") in ("single_choice", "multiple_choice"):
                item["content"] = _normalize_choice_markers(item.get("content", ""))
        # 解析标记后若正文以“证明/证”开头，简答题改标为证明题；选择题、
        # 填空题等保留原题型，避免把解析文字误当题型。
        for item in exercises:
            if (
                item.get("question_type") == "short_answer"
                and re.match(r"^\s*(?:证明|证)(?!明)", item.get("solution_steps", ""))
            ):
                item["question_type"] = "proof"
        # 文末答案区：按小节+题号回填
        if tail_content:
            tail_sections = _split_answer_tail(tail_content)
            if not tail_sections:
                # 没有小节标题的答案区：整段作为一块，按题号全卷匹配
                tail_sections = [
                    {
                        "index": 0,
                        "title": "文末答案",
                        "section_key": None,
                        "question_type": None,
                        "lines": tail_content.split("\n"),
                    }
                ]
            _match_tail_answers(exercises, tail_sections)
        node["exercises"] = exercises
        # 试卷对外也按题型提供独立 section。根节点仍保留整卷 exercises 供
        # 内部兼容/调试，但隐藏根节点的输出，由子节点负责写出各题型结果。
        grouped: dict[str, list[Exercise]] = {}
        for item in exercises:
            key = str(item.get("section_key") or item.get("section") or "简答题")
            grouped.setdefault(key, []).append(item)
        children: list[SectionNode] = []
        root_range = list(node.get("exercise_page_range") or [])
        for key, items in grouped.items():
            pages = [
                page
                for item in items
                for page in (item.get("pages") or [])
                if isinstance(page, int)
            ]
            page_range = [min(pages), max(pages) + 1] if pages else root_range.copy()
            children.append(
                {
                    "title": key,
                    "has_exercise": True,
                    "exampaper": True,
                    "children": [],
                    "exercise_page_range": page_range,
                    "tail_search_used": False,
                    "exercises": items,
                    "_review_source_markdown": review_source_by_key.get(key, ""),
                    "_exampaper_extracted": True,
                }
            )
        node["children"] = children
        node["has_exercise"] = False
        node["_exampaper_extracted"] = True
        logger.debug(
            "[%s] 提取到 %d 道题（%s）",
            node.get("title"),
            len(exercises),
            tail_content and "含文末答案区" or "无文末答案区",
        )


def build_exampaper_sections(
    title: str, doc_page_count: int | None
) -> list[SectionNode]:
    """生成试卷的整卷习题节点：全量 OCR，不走目录识别。

    标题用输入文件名（去扩展名），便于输出 md 命名与报告阅读。
    """
    page_count = doc_page_count or 1
    return [
        {
            "title": title,
            "has_exercise": True,
            "exampaper": True,
            "children": [],
            "exercise_page_range": [1, page_count + 1],
            "tail_search_used": False,
        }
    ]
