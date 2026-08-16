"""试卷（exampaper）专用提取：整卷 OCR、按题型切分、答案/解析处理。

与 textbook / exercise_book 解耦：

- 不走目录识别流程：整卷（一套试卷）全量 OCR，页码范围 [1, N+1)；
- 按“题型小节”切分：识别“一、选择题”“二、填空题”“三、解答题”这类
  小节（兼容早年“二、（本题满分8分）”这种没有题型词的写法），每道题
  打上 question_type（标准英文枚举，见 QUESTION_TYPES）；
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
    r"^\s*[（(]?\s*(\d{1,3})\s*[）)]?\s*[.．、]\s*(.*)$"
)
# 简答题的题号行：17. 内容 / 17、内容（要求点号后有空白，避免把
# “17.2024年…” 里的年份当成多级题号；也避免把行首小数 1.6 当题号）
_SHORT_ANSWER_NUM_RE = re.compile(r"^\s*(\d{1,3})\s*[.．、](?!\d)\s*(.*)$")
# OCR 可能把整份试卷的题号行都渲染成标题（“### 18. （本小题满分…）”），
# 通用“标题还原”规则看到下一个标题就停止扫描，连续标题时还原不了。
# 试卷题型小节是已知的题目区域，直接把“# + 数字 + 分隔符”降级为普通题号行。
_HEADING_PREFIX_RE = re.compile(r"(?m)^#{1,6}\s*")


class TypeSection(TypedDict, total=False):
    """一道试卷里的题型小节（如“一、选择题”）。"""

    index: int          # 小节在文档里的顺序（1 起）
    title: str          # 小节标题全文（去 #，如“一、选择题：1-10 题”）
    section_key: str    # 归一化小节键（如“一、选择题”/“二”），用于答案回填
    question_type: str  # 标准英文枚举（single_choice/multiple_choice/…）
    lines: list[str]    # 小节正文（不含标题行）


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
        if "满分" in rest or "本题共" in rest:
            return True
        if any(marker in rest for marker in ("【解】", "【答案】", "【解析】")):
            # 解析文档里“二、【解】”这类无题型词、无分值的节标题
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
    sections: list[TypeSection] = []
    tail_lines: list[str] | None = None
    current: TypeSection | None = None
    index = 0
    seen_question_section = False

    def flush() -> None:
        nonlocal current
        if current is not None:
            current["lines"] = [l for l in current["lines"] if l.strip()]
            if current["lines"]:
                sections.append(current)
        current = None

    for line in content.split("\n"):
        if tail_lines is not None:
            tail_lines.append(line)
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
                "lines": [],
            }
            seen_question_section = True
            continue
        if current is not None:
            current["lines"].append(line)
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


def _split_simple_numbered_exercises(chunk: str) -> list[Exercise]:
    """按行切分选择题/填空题（题号逐行出现，不用通用多级题号正则）。

    通用切分器会把 “11. 3” 这类答案行误判成多级题号 11.3；
    选择题/填空题的题号始终是单级数字，按行首题号直接切更稳。
    """
    items: list[Exercise] = []
    current: Exercise | None = None
    current_page: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    for line in chunk.split("\n"):
        m_page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m_page:
            current_page = int(m_page.group(1))
            continue
        m = _ITEM_NUM_RE.match(line)
        if m:
            if current is not None:
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
            current["content"] = (current["content"] + "\n" + line).strip()
            end_page = current_page
    if current is not None:
        if start_page is not None and end_page is not None:
            current["pages"] = [start_page, end_page]
        items.append(current)
    return items


def _split_short_answer_exercises(chunk: str) -> list[Exercise]:
    """按行切分简答题（题号行 + 后续行），保留 (1)(2) 子问不拆。

    通用切分器会把 “17. 2024年…” 误判成多级题号 17.2024（年份粘连）；
    简答题题号是单级数字，按行首题号直接切。子问 (1)(2) 不带“数字+点号”
    形态，会作为当前题的正文保留。
    """
    items: list[Exercise] = []
    current: Exercise | None = None
    current_page: int | None = None
    start_page: int | None = None
    end_page: int | None = None
    for line in chunk.split("\n"):
        m_page = re.match(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$", line)
        if m_page:
            current_page = int(m_page.group(1))
            continue
        m = _SHORT_ANSWER_NUM_RE.match(line)
        if m:
            if current is not None:
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
            current["content"] = (current["content"] + "\n" + line).strip()
            end_page = current_page
    if current is not None:
        if start_page is not None and end_page is not None:
            current["pages"] = [start_page, end_page]
        items.append(current)
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
    chunk = demote_exercise_headings(chunk)
    chunk = _HEADING_PREFIX_RE.sub("", chunk)
    chunk, figures = extract_figures(chunk)
    if section["question_type"] in (
        "single_choice",
        "multiple_choice",
        "fill_in_blank",
    ):
        # 只有该节第一道题就是（1）（2）括号编号（早年试卷）时才整体转换；
        # 现代试卷第一道题是 “1.”/“13.”，节内的（1）（2）是子问，不能转
        if _uses_parenthesized_numbering(chunk):
            chunk = _parenthesized_numbers_to_plain(chunk)
        exercises = _split_simple_numbered_exercises(chunk)
    else:
        chunk = _parenthesized_question_numbers_to_plain(chunk)
        exercises = _split_short_answer_exercises(chunk)
    if not exercises:
        pages = _section_pages(section["lines"])
        item: Exercise = {
            "number": "1",
            "separator": ".",
            "content": chunk,
        }
        if pages:
            item["pages"] = pages
        exercises = [item]
    # 题行内的“答：/解：/证明：”拆到 answer/solution_steps（与通用切分器一致）
    for item in exercises:
        content, answer = split_at_line_marker(
            item.get("content", ""), ANSWER_START_RE
        )
        content, solution = _split_solution(content)
        item["content"] = content
        item["answer"] = answer or ""
        item["solution_steps"] = solution or ""
    attach_figures(exercises, figures)
    convert_exercise_tables(exercises)
    for item in exercises:
        item["question_type"] = section["question_type"]
        item["section"] = section["title"]
        item["section_key"] = section["section_key"]
        item["type_index"] = section["index"]
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

    def match_in(block: TypeSection) -> None:
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
        for a in block.get("answers", []):
            number = a.get("number")
            for q in pool:
                if q.get("number") == number:
                    fill_question(q, a)
                    break

    for block in tail_sections:
        block["answers"] = _split_section_exercises(block)
        # 答案区里可能有【解】/【答案】标记，先拆到 answer/solution_steps
        split_inline_answers(block["answers"])
        match_in(block)


def extract_exercises(sections: list[SectionNode]) -> None:
    """试卷版提取入口：整卷切分题型小节，处理内嵌/文末答案，写回 node["exercises"]。"""
    for node in sections:
        if not node.get("exampaper"):
            continue
        content = node.get("exercise_content", "")
        type_sections, tail_content = split_type_sections(content)
        exercises: list[Exercise] = []
        for section in type_sections:
            exercises.extend(_split_section_exercises(section))
        # 内嵌解析：【答案】/【解】拆到 answer / solution_steps
        split_inline_answers(exercises)
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
