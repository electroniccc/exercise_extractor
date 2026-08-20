"""从 markdown 文本中按题号提取习题（与输入来源无关）。

设计目标：本模块只吃 markdown 字符串、吐出结构化的习题列表，不关心输入来自哪里
（PDF 分页 OCR 缓存 / 单个 markdown 文档 / 爬取的网页文本都可以）。将来新增
“直接输入 markdown 文档”的入口时，直接调用 split_exercises 即可。

习题项结构（dict）：
- number:    题号，"1"、"2"...；区间题号（如 "4—6."）为 "4-6"
- content:   题干（不含题号，行首的 "1. " 已被剥掉）
- separator: 题号后的分隔符（"."、"、"、"）"等），输出时还原用
- section:   所属小节名（来自 "# 开头的标题行"），如 "习题1-1（第16页）"
- pages:     [起始页, 结束页]，仅当文本里有 "<!-- page: N -->" 页标记时存在
"""

import re
from typing import TypedDict


class Exercise(TypedDict, total=False):
    """一道习题。number/content 必有；separator/section/pages 视情况存在；
    answer/solution_steps 每个习题都有（无内容时为空字符串）。

    question_type：题型标准英文枚举。课本/习题集由上层流程统一补为
    ``short_answer``；试卷流程按题型小节填写更具体的值。
    """

    number: str
    content: str
    separator: str
    section: str
    pages: list[int]
    question_type: str
    answer: str
    solution_steps: str

# 题号后常见分隔符：半角句点 / 全角句点 / 顿号 / 半角右括号 / 全角右括号（捕获组）
_NUMBER_SEPARATOR = r"([.．、)）])"
# 行首可能出现的 LaTeX/OCR 噪声前缀（星号、上下标标记、数学定界符等），
# 任何教材的 OCR 输出都可能带这类符号；只允许它们存在、不要求必须有。
_EXERCISE_JUNK = r"[*^{}\s$]*"
# 区间型题号："4—6. ..."、"4-6、..."（常见于答案里把若干题合并作答）
EXERCISE_RANGE_RE = re.compile(
    r"^\s*"
    + _EXERCISE_JUNK
    + r"(\d{1,3})\s*"
    + _EXERCISE_JUNK
    + r"[—-]\s*"
    + _EXERCISE_JUNK
    + r"(\d{1,3})\s*"
    + _EXERCISE_JUNK
    + _NUMBER_SEPARATOR
    + r"\*?(?!\d)\s*"
)
# 普通题号："1. 求..."、"1．..."、"1、..."、"1）..."
# （(?!\d) 防止把 "2.5" 之类的小数误判为题号）
EXERCISE_NUM_RE = re.compile(
    r"^\s*"
    + _EXERCISE_JUNK
    + r"(\d{1,3})\s*"
    + _NUMBER_SEPARATOR
    + r"\*?(?!\d)\s*"
)
# 多级题号："1.1.1 证明..."、"1.1 证明..."、"1. 1.7 ..."、"1. 1. 10 ..."
# （OCR 常在点号后加空格）。允许 2 段及以上：不少教材按"章.题"编号
# （如周世勋《量子力学教程》的 "1.1 由黑体辐射公式导出…"）。
# 2 段数字在正文里也可能是节引用（如 "4.9 节的定理"），但不会单独成题：
# 会被后续的小节归属过滤 / 连续性校验 / 节尾匹配规则兜底。
EXERCISE_MULTI_RE = re.compile(
    r"^\s*"
    + _EXERCISE_JUNK
    + r"(\d{1,3}(?:\s*[.．]\s*\d{1,3})+)\s*[.．]?\*?\s*"
)
# markdown 标题行（# 开头）：作为小节边界，重置题号序列
HEADING_RE = re.compile(r"^\s*#")
# 页间标记：由 PDF 分页流程（combine_section_pages）插入，用于页码溯源；
# 纯 markdown 输入不含此标记，此时习题项不带 pages 字段
PAGE_MARKER_RE = re.compile(r"^\s*<!--\s*page:\s*(\d+)\s*-->\s*$")
# 正文编号环境：定理/定义/引理/命题/推论（含英文）。数学教材正文里这类环境
# 独占一行（如“定理 5.1”“定义3.1”），而真实习题的题干/解答里几乎不会出现
# 这样的行。刻意不含“例”：例题在正文叙述里也常成行出现（如“例 1.24 证明…”），
# 用它做判定会误伤小节尾的真实习题。
BODY_ENV_RE = re.compile(
    r"^\s*(定理|定义|引理|命题|推论|"
    r"theorem|definition|lemma|proposition|corollary)"
    r"\s*[.．、:]?\s*\d",
    re.MULTILINE,
)
# OCR 偶尔把题号行渲染成 markdown 标题（如 "### 7.5 设氢原子的状态是"）。
# 只有"多级题号 + 习题动词开头"的标题行才还原为普通题号行，
# 避免误伤真正的章节标题（如 "### 4.5 狄拉克符号"、"### 2. 全同粒子体系"）。
EXERCISE_HEADING_RE = re.compile(
    r"^\s*[*^{}$\s]*\d{1,3}(?:\s*[.．]\s*\d{1,3})+\s*"
    r"(求(?![导])|设|证明|判断|计算|试|若|已知|利用|推导|导出|指出|写出|说明|确定|讨论|"
    r"解(?![析答应决释])|化简|展开|验证|应用|找出|建立|问|求解)"
)
# 题干后的答案段：行首“答”开头（答：/答:/答/[答]/（答）/(答)），该行起到题目
# 结束都是答案。裸“答”分支用负向断言排除“答案/答题/答 案”等非答案段字样。
ANSWER_START_RE = re.compile(
    r"^\s*(?:"
    r"[\[（(]\s*答\s*[\])）)][：:]?"
    r"|答(?!\s*[\u4e00-\u9fff])[：:]?"
    r")\s*"
)
# 解答过程段：行首“解：”/“解.”/“解．”/“[解]”/“（解）”/(解) 开头。
# “解”后必须是冒号/句号/空白（避免把“解析/解决/解释/解得”等正文字样误判），
# 带括号的写法本身无歧义。
SOLUTION_START_RE = re.compile(
    r"^\s*(?:"
    r"[\[（(]\s*解\s*[\])）)][：:]?"
    r"|解[：:.．\s]"
    r")\s*"
)
# “证明/证”开头的解答段：“证明. / 证明： / 证. / 证： / 解空格”等
PROOF_START_RE = re.compile(
    r"^\s*(?:证明(?!题)(?:[.．：:]|\s)|证(?!明)(?:[.．：:]|\s))\s*"
)


def page_marker(page_num: int) -> str:
    """生成页间标记文本（与 PAGE_MARKER_RE 对应，单一事实来源）。"""
    return f"<!-- page: {page_num} -->"


def parse_number_key(num_str: str) -> tuple[int, ...] | None:
    """题号转整数元组："1.1.7" -> (1, 1, 7)，"1" -> (1,)。解析失败返回 None。"""
    try:
        return tuple(int(p) for p in num_str.split("."))
    except ValueError:
        return None


def numbers_are_consecutive(numbers: list[str]) -> bool:
    """题号序列是否基本连续（用于判定某区域是否真是题目区域）。

    相邻两项要求：前缀相同且末位递增 1~2（允许 OCR 漏掉个别题号）；
    少于 2 道题或出现乱序/大跳号则判为不连续。
    """
    if len(numbers) < 2:
        return False
    keys = [parse_number_key(n) for n in numbers]
    if any(k is None for k in keys):
        return False
    ok = 0
    for a, b in zip(keys, keys[1:]):
        if len(a) == len(b) and a[:-1] == b[:-1] and 1 <= b[-1] - a[-1] <= 2:
            ok += 1
    return ok / (len(numbers) - 1) >= 0.5


def normalize_section(text: str) -> str:
    """归一化小节名用于比较：忽略空白与 LaTeX/星号噪声。

    目录标题可能是 "*习题 7-10"，页内 OCR 标题可能是 "##  $ ^{*} $习题 7-10"，
    两者都应归一到 "习题7-10"。目录和 OCR 对区间题号使用的横线字符
    也可能不同（如 ``1–5`` 与 ``1-5``），统一为 ASCII 连字符。
    """
    normalized = re.sub(r"[‐‑‒–—−－~～]", "-", text or "")
    return re.sub(r"[\s*^{}$]+", "", normalized)


# 泛化习题字样：只有 "习题/练习/作业" 之类，不带编号或知识点
_GENERIC_SECTION_RE = re.compile(
    r"^(练习题|习题|练习|作业|exercise|exercises|problem|problems|"
    r"question|questions|practice|homework)"
    r"(答案|参考答案|answers?|solutions?)?$",
    re.IGNORECASE,
)


def is_generic_section(section: str | None) -> bool:
    """section 是否只是泛化的习题字样（如 "练习题"），不体现具体知识点。"""
    if not section:
        return False
    return bool(_GENERIC_SECTION_RE.match(normalize_section(section)))


def truncate_to_section_heading(content: str, section_title: str) -> str:
    """截取从“与本小节标题匹配的标题行”开始的内容。

    用于节尾搜索的防御：小节较短时，节尾区间会前伸到上一节末尾，
    找到本小节标题（如 "### 1.6 曲线和域"）所在行，丢弃其前面的内容；
    找不到匹配标题时原样返回。
    """
    title_norm = normalize_section(section_title)
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if HEADING_RE.match(line):
            heading_text = re.sub(r"^#+\s*", "", line)
            if normalize_section(heading_text) == title_norm:
                return "\n".join(lines[i:])
    return content


def truncate_before_heading(content: str, section_title: str) -> str:
    """截取到“与指定标题匹配的标题行”之前的内容（不含该行）。

    用于隐式节尾搜索：节尾区间可能伸入下一个目录节点的起始页，
    找到边界节点的标题行（如 "### §1.2 复变函数"）后丢弃其后的内容，
    避免把下一节的内容当成本小节的节尾习题；找不到匹配标题时原样返回。
    """
    title_norm = normalize_section(section_title)
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if HEADING_RE.match(line):
            heading_text = re.sub(r"^#+\s*", "", line)
            if normalize_section(heading_text) == title_norm:
                return "\n".join(lines[:i])
    return content


def truncate_before_page_heading(content: str, page_num: int) -> str:
    """截取到指定页（<!-- page: N --> 标记定位）的第一个标题行之前。

    边界标题匹配的兜底：目录标题与页内标题写法可能不一致
    （如目录 "2 连续函数" vs 页内 "## 第2章 连续函数"），
    此时按边界页本身定位，把边界页上的第一个标题行（即下一节的标题）
    之前的内容保留、之后的内容丢弃；找不到该页或该页没有标题时原样返回。
    """
    lines = content.split("\n")
    on_page = False
    for i, line in enumerate(lines):
        m_page = PAGE_MARKER_RE.match(line.strip())
        if m_page is not None:
            on_page = int(m_page.group(1)) == page_num
            continue
        if on_page and HEADING_RE.match(line):
            return "\n".join(lines[:i])
    return content


def truncate_from_page_heading(content: str, page_num: int) -> str:
    """截取从指定页（<!-- page: N --> 标记定位）的第一个标题行开始的内容。

    起始页标题兜底：目录标题可能比页内标题多前缀（如目录 "习题 1-2 数列的极限"
    vs 页内 "## 数列的极限"），标题匹配失败时按起始页本身的第一个标题行
    （即本节的标题）截取，丢弃该页标题之前的上一节内容；找不到该页或该页
    没有标题时原样返回。
    """
    lines = content.split("\n")
    on_page = False
    for i, line in enumerate(lines):
        m_page = PAGE_MARKER_RE.match(line.strip())
        if m_page is not None:
            on_page = int(m_page.group(1)) == page_num
            continue
        if on_page and HEADING_RE.match(line):
            return "\n".join(lines[i:])
    return content


def demote_exercise_headings(content: str) -> str:
    """把被 OCR 渲染成标题的多级题号行还原为普通题号行。

    例："### 7.5 设氢原子的状态是" → "7.5 设氢原子的状态是"，
    "#### 4.1.1 证明数列" → "4.1.1 证明数列"。
    这样题号行能参与习题切分，而不是把后续题目都归到一个错误的小节名下。
    """
    lines = content.split("\n")
    changed = False
    for i, line in enumerate(lines):
        if HEADING_RE.match(line):
            text = re.sub(r"^#+\s*", "", line)
            if EXERCISE_HEADING_RE.match(text):
                lines[i] = text
                changed = True
    return "\n".join(lines) if changed else content


def split_at_line_marker(
    content: str,
    marker_re: re.Pattern[str],
    allow_first_line: bool = True,
) -> tuple[str, str | None]:
    """按行首标记（如“答：”“解：”）把内容切成 (标记前段, 标记段及之后)。

    返回 (前段, 标记段)；没有标记时返回 (原内容, None)。
    标记段从第一个匹配行开始直到内容末尾，可能跨多行/公式块；
    若标记是内容的第一行（没有前段），不切分，原样保留。
    allow_first_line=False 时，标记若出现在第一个非空行（如证明题题干
    开头的"证明：…"）则跳过继续找后面的标记行。
    """
    lines = content.split("\n")
    first_nonblank = next(
        (i for i, l in enumerate(lines) if l.strip()), None
    )
    for i, line in enumerate(lines):
        if marker_re.match(line) and (
            allow_first_line or i != first_nonblank
        ):
            head = "\n".join(lines[:i]).strip()
            rest = marker_re.sub("", "\n".join(lines[i:]), count=1).strip()
            if head:
                return head, rest
            if allow_first_line:
                break
    return content.strip(), None


def split_answer_from_content(content: str) -> tuple[str, str | None]:
    """把题干与行首“答”开头的答案段分开（见 split_at_line_marker）。"""
    return split_at_line_marker(content, ANSWER_START_RE)


def _split_solution(content: str) -> tuple[str, str | None]:
    """把解答过程从题干中切出（支持“解”标记与“证明/证”标记）。

    “证明/证”标记的特殊规则（证明题的题干里也常有“证明：…”）：
    - 内容里有多个行首“证明/证”标记时，题干中靠前的“证明：…”是要证的结论，
      解答通常以最后一个“证明/证”开头 → 从最后一个证明标记开始切；
    - 只有一个“证明/证”标记且是第一个非空行时（纯证明题题干），不切；
    - 只有一个“证明/证”标记但不是第一个非空行（超甜风格：题尾独立解答），切。
    其余情况交给“解”标记的通用切分。
    """
    lines = content.split("\n")
    proof_idxs = [i for i, l in enumerate(lines) if PROOF_START_RE.match(l)]
    if len(proof_idxs) >= 2:
        i = proof_idxs[-1]
        head = "\n".join(lines[:i]).strip()
        if head:
            rest = PROOF_START_RE.sub("", "\n".join(lines[i:]), count=1).strip()
            return head, rest
    if len(proof_idxs) == 1:
        first_nonblank = next((i for i, l in enumerate(lines) if l.strip()), None)
        if proof_idxs[0] != first_nonblank:
            i = proof_idxs[0]
            head = "\n".join(lines[:i]).strip()
            rest = PROOF_START_RE.sub("", "\n".join(lines[i:]), count=1).strip()
            return head, rest
    return split_at_line_marker(content, SOLUTION_START_RE, allow_first_line=False)


def drop_runs_not_reaching_section_end(
    exercises: list[Exercise],
) -> list[Exercise]:
    """数字序号习题必须匹配到小节结尾：丢弃正文中间的分类叙述。

    若一个小节（两个标题行之间）最后一题的正文里还出现“定理/定义/引理/命题/
    推论”这类编号环境，说明 1. 2. 3. 只是小节正文里的分类叙述（如
    “1. 可去奇点、2. 极点、3. 本性奇点”），并不是习题。整组丢弃，防止把
    正文中间的内容当成习题提取出来。
    """
    kept: list[Exercise] = []
    i = 0
    while i < len(exercises):
        j = i + 1
        while (
            j < len(exercises)
            and exercises[j].get("section") == exercises[i].get("section")
        ):
            j += 1
        group = exercises[i:j]
        last = group[-1]
        if last.get("number") == "__preamble__" or not BODY_ENV_RE.search(
            last.get("content", "")
        ):
            kept.extend(group)
        i = j
    return kept


def _parse_number_label(text: str) -> tuple[str, tuple[int, ...]] | None:
    """解析行首题号（允许 OCR 噪声前缀）："3."→("3",(3,))，"3-5."→("3-5",(3,))，
    "3.5"→("3.5",(3,5))。解析失败返回 None。"""
    m = EXERCISE_MULTI_RE.match(text)
    if m:
        label = m.group(1).replace("．", ".")
        return label, tuple(int(p) for p in label.split("."))
    m = EXERCISE_RANGE_RE.match(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}", (int(m.group(1)),)
    m = EXERCISE_NUM_RE.match(text)
    if m:
        return m.group(1), (int(m.group(1)),)
    return None


def _keys_continuous(a: tuple[int, ...], b: tuple[int, ...]) -> bool:
    """b 是否紧接 a：段数相同、前缀相同、末位递增 1~2（允许 OCR 漏号）。"""
    return (
        len(a) == len(b)
        and a[:-1] == b[:-1]
        and 1 <= b[-1] - a[-1] <= 2
    )


def _heading_is_ocr_number(
    line: str,
    lines: list[str],
    idx: int,
    prev_key: tuple[int, ...],
) -> bool:
    """判断标题行是否是被 OCR 误转的题号行（如 "### 3. 回答下列问题："）。

    全部满足才认定：
    1. 标题文字行首能解析出题号（"3."、"3-5."、"3.5" 等）；
    2. 已出现过题号时，与当前题号序列同模式（段数相同）且连续；
    3. 标题之后（到下一个标题前）还有同模式且连续的题号。
    """
    text = re.sub(r"^#+\s*", "", line)
    parsed = _parse_number_label(text)
    if parsed is None:
        return False
    _, key = parsed
    if prev_key:
        if len(key) != len(prev_key) or not _keys_continuous(prev_key, key):
            return False
    # 向后找同模式且连续的题号（到下一个标题/文末为止，限制扫描窗口）
    for j in range(idx + 1, min(idx + 1 + 40, len(lines))):
        nxt = lines[j]
        if HEADING_RE.match(nxt):
            break
        nxt_parsed = _parse_number_label(nxt)
        if nxt_parsed is None:
            continue
        _, nxt_key = nxt_parsed
        if (
            len(nxt_key) == len(key)
            and nxt_key > key
            and _keys_continuous(key, nxt_key)
        ):
            return True
    return False


def split_exercises(
    content: str,
    keep_leading: bool = True,
    check_section_end: bool = True,
) -> list[Exercise]:
    """按题号把 markdown 文本分割成一道道习题。

    规则（结构驱动，不针对特定教材）：
    - 行首 "N + 分隔符" 且题号单调递增 → 新题
    - "# 开头的标题行" → 结束当前题、重置题号序列，标题记为小节名（section）
    - 子题 (1)（2）(a)、公式、表格等一律归到当前题
    - 没有任何编号时，整段内容存为 {"number": "__preamble__"}

    keep_leading=False 时（隐式节尾搜索用），第一道题前的正文内容直接丢弃：
    节尾区间刻意从上一节正文/上一题解答的中段开始，这些内容不是本小节的题。

    check_section_end=False 时（显式习题节点 / 习题集），不做“数字题必须匹配到
    小节结尾”的后置校验：该规则用于剔除节尾搜索区间里正文的编号列表，
    但已知是习题的区域里解答常引用“定理 N/定义 N”，会误伤整组题目。

    注意：有的章节会包含多组独立编号（如整章习题答案里每组答案都从 1 重新开始），
    所以用列表保存而不是 dict——dict 的键会因重复而互相覆盖。
    """
    exercises: list[Exercise] = []
    pending: list[str] = []  # 尚未归属任何习题的标题行
    current: list[str] = []  # 当前习题的文本行
    current_num: str | None = None
    current_sep: str | None = None
    current_section: str | None = None
    prev_key: tuple[int, ...] = ()  # 上一道题的题号（整数元组），用于单调性判断
    leading: list[str] = []  # 第一道题之前的内容（可能是上一题的尾巴，也可能是正文噪声）
    current_page: int | None = None  # 最近一次页码标记
    exercise_start_page: int | None = None
    exercise_end_page: int | None = None

    def flush() -> None:
        nonlocal current, current_num, current_sep, current_section
        nonlocal exercise_start_page, exercise_end_page
        if current_num is not None:
            text = "\n".join(current).strip()
            if text:
                item: Exercise = {"number": current_num, "content": text}
                if current_sep is not None:
                    item["separator"] = current_sep
                if current_section:
                    item["section"] = current_section
                if (
                    exercise_start_page is not None
                    and exercise_end_page is not None
                ):
                    item["pages"] = [exercise_start_page, exercise_end_page]
                exercises.append(item)
            current = []
            current_num = None
            current_sep = None
            exercise_start_page = None
            exercise_end_page = None

    # 统一换行符，避免 \r\n / \r 影响行首匹配
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = content.split("\n")
    for idx, line in enumerate(lines):
        m_page = PAGE_MARKER_RE.match(line)
        if m_page:
            current_page = int(m_page.group(1))
            continue

        if HEADING_RE.match(line):
            if _heading_is_ocr_number(line, lines, idx, prev_key):
                # 题号行被 OCR 误转成标题（如 "### 3. 回答下列问题："）：
                # 去掉 # 还原为普通行，走下面的题号匹配逻辑
                line = re.sub(r"^#+\s*", "", line)
            else:
                # 新的章节标题：结束当前题，并把题号序列重置（每组独立编号都从 1 重新开始）
                flush()
                pending.append(line)
                prev_key = ()
                continue

        # 多级题号（"1.1.1"、"1. 1.7"、"1. 1. 10"）优先匹配
        m_multi = EXERCISE_MULTI_RE.match(line)
        if m_multi:
            parts = [
                p.strip() for p in m_multi.group(1).replace("．", ".").split(".")
            ]
            label = ".".join(parts)
            num_key: tuple[int, ...] | None = tuple(int(p) for p in parts)
            sep = ""  # 多级题号自带点号，输出时不需要额外分隔符
            stripped = line[m_multi.end():]
        else:
            m_range = EXERCISE_RANGE_RE.match(line)
            if m_range:
                label = f"{m_range.group(1)}-{m_range.group(2)}"
                num_key = (int(m_range.group(1)),)
                sep = m_range.group(3)
                stripped = line[m_range.end():]
            else:
                m_num = EXERCISE_NUM_RE.match(line)
                if m_num:
                    label = m_num.group(1)
                    num_key = (int(m_num.group(1)),)
                    sep = m_num.group(2)
                    stripped = line[m_num.end():]
                else:
                    num_key = None
                    label = None
                    sep = None
                    stripped = None

        if num_key is not None and num_key > prev_key:
            flush()
            # pending 里的标题行作为“所属小节”记录，不再拼进题干；
            # 行首的 LaTeX/星号噪声（如 “##  $ ^{*} $习题 7-10”）一并清掉，
            # 便于与目录标题（“*习题 7-10”）做归一化匹配
            if pending:
                section_line = re.sub(r"^#+\s*", "", pending[-1])
                section_name = re.sub(r"^[*^{}\s$]+", "", section_line).strip()
                current_section = section_name or None
                pending = []
            current = []
            if keep_leading and not exercises and leading and num_key[0] > 1:
                # 内容从上一题中间开始（如先是一堆 (1)(2)... 子项、再出现 2.）时，
                # leading 是上一题的尾巴，接到第一道题前面；若第一道题是 1. 则丢弃
                current = leading
            leading = []
            current.append(stripped)
            current_num = label
            current_sep = sep
            prev_key = num_key
            exercise_start_page = current_page
            exercise_end_page = current_page
        else:
            # 子题 (1)、公式、表格等，或题号未递增（OCR 重复）的情况，都归到当前题
            if not exercises and current_num is None:
                leading.append(line)
            else:
                current.append(line)
                exercise_end_page = current_page

    flush()
    # 末尾悬空的标题（后面没有题号）不属于任何习题，丢弃；
    # 若整个内容都没有编号，则原样存到 __preamble__，避免信息丢失
    if pending and not exercises:
        exercises.append(
            {"number": "__preamble__", "content": "\n".join(pending).strip()}
        )
    if check_section_end:
        exercises = drop_runs_not_reaching_section_end(exercises)
    # 题干/解答过程/答案分离：行首“解：”段 -> solution_steps，行首“答：”段 -> answer。
    # 每个习题都保证带这两个属性，没有相应内容的置空字符串。
    for item in exercises:
        if item.get("number") == "__preamble__":
            continue
        content, answer = split_at_line_marker(item["content"], ANSWER_START_RE)
        content, solution = _split_solution(content)
        item["content"] = content
        item["answer"] = answer or ""
        item["solution_steps"] = solution or ""
    return exercises


def filter_exercises_by_section(
    exercises: list[Exercise], section_title: str
) -> list[Exercise]:
    """只保留属于指定小节的习题（相邻小节页码重叠时用于区分归属）。

    归一化比较（忽略空白与 LaTeX/星号噪声）；若没有任何习题带上该小节名
    （OCR 漏掉小节标题等），则保留全部，避免误删。
    """
    title_norm = normalize_section(section_title)
    section_norms = {
        normalize_section(e.get("section")) for e in exercises
    }
    if title_norm in section_norms:
        return [
            e
            for e in exercises
            if normalize_section(e.get("section")) == title_norm
        ]
    return exercises
