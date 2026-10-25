"""基础审校：按模板修复 OCR 文字、标点与 KaTeX/LaTeX 格式，只返回 Markdown。"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any

from ..llm_usage import UsageCallback, report_usage, usage_from_response

logger = logging.getLogger(__name__)

# OpenAI-compatible endpoint and default model from DeepSeek's official API.
DEFAULT_REVIEW_MODEL = "deepseek-flash"
DEFAULT_REVIEW_BASE_URL = "https://api.deepseek.com"
DEFAULT_REVIEW_TIMEOUT_SECONDS = 180
DEFAULT_REVIEW_REASONING_EFFORT = "none"

# exercise_extractor 的 Markdown 输出格式：*1.*、*4-6.*、*1.2.3.* 题目……
_EXERCISE_START_RE = re.compile(
    r"(?m)^\s*\*?((?:\d+(?:\.\d+)+|\d+(?:\s*[—–－-]\s*\d+)?))"
    r"\s*\\?[.．、)）]\s*\*?\s+"
)
_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+\S.*$")
_ANSWER_MARKER_RE = re.compile(
    r"(?m)^\s*(?:\*\*\s*)?(解答|答案|解析|详解|解|答)(?:\s*\*\*)?\s*(?:[：:]|(?=[（(]\s*\d{1,3}\s*[）)]))\s*"
)
_NUMBERED_ANSWER_START_RE = re.compile(
    r"(?m)^[ \t]*(?:\$\$[^\n]*?&[ \t]*)?[（(]\s*(\d{1,3})\s*[）)]"
)
_MATH_SPAN_RE = re.compile(
    r"(?s)(?<!\\)\$\$(.*?)(?<!\\)\$\$|(?<!\\)\$(.*?)(?<!\\)\$"
)
_LATEX_COMMAND_RE = re.compile(r"\\([A-Za-z]+|[^A-Za-z\s])")
_LATEX_ENV_RE = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}(?:\s*\{[^{}]*\})?")

_SEMANTIC_LATEX_COMMANDS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh", "ln", "log", "lg", "exp", "lim", "max", "min",
    "sup", "inf", "det", "gcd",
}

MIN_REVIEW_TEXT_SIMILARITY = 0.35

BASIC_REVIEW_PROMPT_TEMPLATE = r"""下面是对一个扫描版PDF文档做OCR后提取的部分结果：

- 文档：{document_name}
- 小节：{section_name}
- 提取内容：该小节的习题与对应答案
- 内容格式：嵌有LaTeX的Markdown

请对这部分内容做校对和整节整理，最终只输出整理后的Markdown正文本身，不要添加任何解释、总结或前后缀说明。

### 0. 整节题目与答案整理
- 先通读整节，再判断每段题干和已有解答/答案分别属于哪道题；扫描或 OCR 可能把多个小题写在同一道题下，也可能让答案与题目错位。
- 仅将原文中已经出现的解答/答案段移动到对应题目下面；必须保留原答案文字与公式，不得求解、推导、补写、扩写、删减或合并答案。
- `**解**`/`**证**` 后的已有内容必须逐段完整保留，包括所有括号小问、公式块、分步推导和结论；只修正格式或明显 OCR 错误，不得总结、缩写，也不得处理完第一个小问就省略后续内容。
- 严格区分解题过程与最终答案：有推理、计算步骤或证明正文的内容放入 solution_steps；只有原文明确给出的独立答案/答案区，或题目后仅有简短结果且没有推理过程时，才填 answer。详细过程末尾出现的结果仍属于过程，不得据此另造一个简短答案。
- 如果原文只有详细解答过程（包括完整证明），answer 必须为空；如果原文只有简短结果，放入 answer，solution_steps 留空。绝不为缺少答案的题求解或从过程推导答案。
- 保留原题号和题干顺序。每题使用独立的 `*题号.*` 开头；普通解答使用 `**解**:`，证明过程使用 `**证**:`，最终答案使用 `**答**:`。
- 将“解法/解答”类过程规范为 `**解**`，将“证明/证法”类过程规范为 `**证**`。同一道题的多种解法必须保存在一个解答段中，依次用 `*法1*`、`*法2*` 等分隔；不要把不同解法拆成多道题。
- OCR 可能把解答起始词与正文粘在一起，例如题干结束后另起一段写“解作n维球坐标变换”或“解由于…”。请结合前后文判断：这里的“解”是已有解答的标记，“作n维球坐标变换”等后续文字、公式和推导都属于同一道题的解题过程。输出时改为独立的 `**解**:` 段，保留“解”后每个字和全部公式；证明过程同理规范为 `**证**:`。不要仅凭出现“解”字就拆分题干。
- OCR 也可能把独立的“解”误放在公式环境开头，例如 `$$ \begin{aligned}& 解 \quad\oint_L\cdots`。这时“解”是公式外的解答标记；把它移到公式块前成为独立的 `**解**:`，原公式及其后续说明完整放入解答，不要把公式留在题干或删掉。
- OCR 原文中的 `(1)`、`(2)`、`1)` 等答案编号通常对应题号 `*1.*`、`*2.*`；应按编号逐题归属，并保留答案里的原编号文字。
- 不能可靠判断答案归属时，保留该段原文及其相对顺序，不要猜测或丢弃。
- 整理完成后，确保全部原题、公式和已有答案仍在输出中，且没有重复内容。

## 校对规则

### 1. 逐题检查
逐题检查题目和答案（如果有）的文字内容是否有OCR识别错误（错别字、数字/符号误识别等），如有明显错误予以修正。

### 2. 标点符号规范
- 中文叙述部分使用全角标点（，。；：""''）。
- 公式内部、变量名、单位符号、以及数学表达式与其后紧邻的标点之间，使用半角符号（不要把行内公式后面的句号写成全角）。
- 行内公式与中文文字之间不加空格；行内公式与英文单词之间加一个半角空格。
- 英文内容按英文大学教材惯例处理标点（如句末句号后单空格、em-dash用法等）。
- 全篇标点风格必须统一，不要一部分用全角一部分用半角。

### 3. LaTeX定界符修复与统一

最终输出中，所有数学表达式必须且只能使用以下两种Markdown数学定界符：

行内公式：统一使用 $...$。
独立成行的公式：统一使用 $$...$$。

除上述两种形式外，不允许使用任何其他数学定界符。具体要求：

若原文或OCR结果中出现 \(...\)，必须转换为 $...$。
若原文或OCR结果中出现 \[...\]，必须转换为 $$...$$。
修复 $ 或 $$ 定界符缺失、单侧缺失、多余、重复嵌套或不配对的情况。
禁止输出 $\( ... \)$、$$\[ ... \]$$ 等重复嵌套的数学定界符。
$...$ 内部不得再出现 \(、\)；$$...$$ 内部不得再出现 \[、\]。
不要因为统一定界符而改变公式内容，也不要合并或拆分本应独立的数学表达式。
此规则针对最终输出格式，即使输入中的 \(...\)、\[...\] 本身是合法LaTeX，也必须转换为上述统一格式。

### 4. LaTeX语法修复
检查LaTeX内容是否有语法错误（如括号不匹配、命令拼写错误、上下标缺失等），在**不改变原有数学含义**的前提下修复。若无法判断某处是OCR错误还是原文本身如此，保守处理，不做改动，并可用行内注释 `<!-- 待确认 -->` 标出（不要擅自修正数学结果）。

### 5. KaTeX兼容性
按以下规则调整，使LaTeX尽可能兼容KaTeX渲染：
- `\iint`、`\oiint`、`\oint` 等多重/曲线积分符号如KaTeX原生支持则保留，若涉及需额外宏包的命令，改写为等价的基础命令组合。
- 不要裸用 `align`、`align*` 环境；如需多行公式对齐，使用 `aligned`（写在 `$$...$$` 内部）。
- 避免使用 `\dfrac`，统一用 `\frac{}{}`；仅当表达式明确要求"独立成行的大字号分数"时使用 `\dfrac`。
- 乘号统一使用 `\cdot`（标量乘法）或 `\times`（叉乘/数量级，如 `10^6`），不要混用表达同一含义的乘号。
- 避免使用非核心宏包命令（如mathtools、physics包的命令），改用KaTeX原生支持的等价写法。

### 6. 编号与风格统一
- 一个大题下若有多个小题，因OCR丢失小题号的，尝试根据上下文补出（如"(1)(2)(3)"），严格保持原文的编号体系风格（不要把"(1)"改成"①"或反过来）。
- 若同一大题下每个小题是独立的LaTeX块，判断该题内容的呈现方式：
  - 若原文各小题都是嵌在文字叙述中的短表达式，统一用单 `$...$` 包裹；
  - 若原文各小题本身是独立成行的完整算式/极限式，统一用 `$$...$$` 包裹。
  - 统一原则：同一大题下所有小题必须使用同一种包裹方式，不能一半单$一半双$。

### 7. 答案完整性边界（禁止扩写）
- 若某题没有提取到答案，或答案原文为"略"，**保持原样，不要代为求解或补全**。
- 不要为了让格式好看而删除"略"或空答案标记。

### 8. 图片与无关标签
若出现图片占位标签（如 `[图片]`、`![](...)`），原样保留位置但不处理其内容，只专注校对文字与公式部分。

### 9. 格式规范化（用于保证多次运行结果一致，便于后续去重hash比对）
- 分数、根号、上下标的书写方式在同类表达式中保持完全一致的LaTeX写法（例如同一小节内所有分数都用 `\frac`，不要一部分用 `\frac`一部分用手写斜杠形式）。
- 数字与单位之间是否加空格、极限符号 `\lim_{n \to \infty}` 的书写方式（箭头统一用 `\to` 不用 `\rightarrow`）等，在全篇范围内保持唯一固定写法。
- 输出中不要引入原文没有的多余空行或额外的Markdown装饰（如加粗、分割线），除非原文本身包含这些结构。

## 保守修改总原则
对于任何无法确定是OCR错误还是原文本身如此的地方（尤其是数学表达式的符号、系数、上下标），优先保持原文不变，不要为了"看起来更规范"而改变原始数学含义或引入你自己的推断结果。

<markdown>
{content}
</markdown>
"""

MATCH_ANSWERS_PROMPT_TEMPLATE = r"""请整理下面这一节内容，先完成题目与答案匹配。此轮只判断归属，不修改题目、推导或公式。

<current-draft>
这是用户当前正在编辑的版本，优先级最高。若它与 OCR 参考内容冲突，必须保留当前稿中的题干、解答和答案；不能用 OCR 旧内容覆盖。
{draft}
</current-draft>

<ocr-reference>
仅用于从当前稿中找回确实漏掉的已有解答或答案；不要把它当作题干或答案的替代正文。
{reference}
</ocr-reference>

原文中的 (1)、(2) 等题号和答案编号对应同一道题。每一道题都返回 number、question、solution_steps、answer 四个字符串字段：
- question 只放题干，不放解析、答案或评分表；题号放在 number 字段。
- solution_steps 放原文已有的推导/证明过程，不包括“解答/证明”等外层标签或独立的最终答案；证明过程仍放在唯一的 solution_steps 字段中。
- 题干后若出现未加标点的解答开头，例如“解作n维球坐标变换”，应按上下文将“作n维球坐标变换”及后续完整推导、公式移入 solution_steps，question 只保留此前的题目要求；这只是移动已有内容，不能补写推导。
- 若 OCR 将解答标记放进题干后的独立公式块开头（如 `$$ \begin{aligned}& 解 \quad...`），将“解”视为公式外的标签，整块公式及其后续说明放入 solution_steps；question 在此公式块前结束。保留全部已有公式与说明，不新增推导。
- 同一道题如有多个解法，将所有方法合并在 solution_steps 字符串中，使用 `*法1*`、`*法2*` 等 Markdown 标记方法边界，不要拆分或丢弃。
- answer 只放原文明示的独立最终结果，或题目后唯一的简短结果（没有推理步骤时）；不要从解答/证明的末尾复制或推导答案。
- 若只有最终结果，没有推导，solution_steps 为空；若只有推导/证明过程而没有单独答案，answer 为空。
- 不得遗漏任何题目、公式和已有答案；保留题号顺序。

只输出合法 JSON 对象，格式为 {"exercises":[{"number":"1","question":"...","solution_steps":"...","answer":"..."}]}。LaTeX 反斜线必须按 JSON 规则转义。

文档：{document_name}
小节：{section_name}"""

FORMAT_ANSWERS_PROMPT_TEMPLATE = r"""下面是已经匹配好题号、题干、解答过程和最终答案的一节试卷。请做第二轮校对：修正明显 OCR 文字、标点和 LaTeX/KaTeX 公式错误，统一行内 $...$ 与独立 $$...$$ 定界符。

保持 exercises 的题数、题号、顺序和四个字段的职责。question 只放题干，solution_steps 只放原文已有的推导或证明正文（不含外层 `**解**`/`**证**` 标签），answer 只放原文明示的独立最终答案，或题目后唯一的简短结果（没有推理步骤时）。若原文只有解题过程/证明过程，即使过程末尾得出结果，也必须保持 answer 为空；不得从过程复制或推导答案，不得为证明题额外添加“答”。若原文只有简短结果，则将它放入 answer，solution_steps 留空。不能凭题目自行求解，也不能删掉已有推导或答案。
如果第一轮仍把“解作n维球坐标变换”这类题干后粘连的解答留在 question，必须在本轮把“解”后的已有文字及全部公式移入 solution_steps；保留原有步骤，不添加新步骤或独立答案。
如果第一轮仍把公式块开头的“解”（如 `$$ \begin{aligned}& 解 \quad...`）留在 question，也必须把“解”移到公式外作为标签，将整块公式及其后续说明移入 solution_steps；不得遗漏原文。
同一道题存在多个解法时，必须全部保存在同一个 solution_steps 字符串里，并依次用 `*法1*`、`*法2*` 等分隔；不可为不同解法新增 exercise。证明题仍使用同一个 solution_steps 字段，最终 Markdown 外层标签由题目类型规范为 `**证**`。
question、solution_steps、answer 都是 Markdown 字符串：每个行内数学公式必须用单个 $...$ 包裹，独立公式必须用 $$...$$ 包裹。严禁输出裸 LaTeX；例如 \frac{3}{2} 必须写成 $\frac{3}{2}$。输出 JSON 时只做 JSON 所需的反斜线转义，解码后的 Markdown 仍需保留正确的公式定界符。
所有 $$...$$ 公式必须独立成段、单独占行，前后各留一个空行；禁止把 $$...$$ 放在中文句子或解答标记的同一行。

只输出合法 JSON 对象，格式为 {"exercises":[{"number":"1","question":"...","solution_steps":"...","answer":"..."}]}。LaTeX 反斜线必须按 JSON 规则转义。

文档：{document_name}
小节：{section_name}
第一轮匹配结果：
<matched>
{content}
</matched>

当前编辑稿（与第一轮匹配的主来源）：
<current-draft>
{draft}
</current-draft>

原始 OCR（仅供找回当前稿中漏掉的已有内容；与当前稿冲突时以当前稿为准）：
<ocr-reference>
{reference}
</ocr-reference>"""



def _exercise_segments(markdown: str) -> list[str]:
    """按题号切出题目片段，供审校前后的保守比较使用。"""
    starts = list(_EXERCISE_START_RE.finditer(markdown))
    return [
        markdown[start.start() : starts[index + 1].start() if index + 1 < len(starts) else len(markdown)]
        for index, start in enumerate(starts)
    ]


def _recover_escaped_numbered_exercises(
    source: str, parsed_numbers: list[str]
) -> list[dict[str, str]]:
    """从明确转义的题号恢复边界，不依赖已受损的整节切分状态。"""
    from ..parse_md_exercises import split_exercises

    starts = list(_EXERCISE_START_RE.finditer(source))
    numbers = [match.group(1) for match in starts]
    if len(numbers) <= len(parsed_numbers) or not all(number.isdecimal() for number in numbers):
        return []
    if any(int(left) >= int(right) for left, right in zip(numbers, numbers[1:])):
        return []
    if not set(parsed_numbers).issubset(numbers):
        return []
    missing = [match for match in starts if match.group(1) not in parsed_numbers]
    if not missing or not all(
        re.search(r"\\[.．、)）]", match.group(0)) for match in missing
    ):
        return []

    recovered: list[dict[str, str]] = []
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(source)
        body = source[match.end() : end].strip()
        segment = f"*{match.group(1)}.* {body}"
        items = [
            item for item in split_exercises(segment, check_section_end=False)
            if item.get("number") != "__preamble__"
        ]
        if len(items) != 1 or items[0]["number"] != match.group(1):
            return []
        recovered.append({
            "number": items[0]["number"],
            "question": items[0]["content"],
            "solution_steps": items[0].get("solution_steps") or "",
            "answer": items[0].get("answer") or "",
        })
    return recovered


def _math_literal_signature(segment: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """提取数学块中的文字/数字和语义命令序列。

    只忽略明确属于排版的 TeX 命令和环境名；因此 ``f^m`` 变成
    ``f^{(6)}`` 会被拦截，而空格、定界符、``array``/``cases`` 等排版
    修复仍可通过。它不是数学等价性检查，只是阻止基础审校替 OCR 猜字。
    """
    spans = [match.group(1) or match.group(2) for match in _MATH_SPAN_RE.finditer(segment)]
    literals: list[str] = []
    commands: list[str] = []
    for span in spans:
        command_names = [match.group(1) for match in _LATEX_COMMAND_RE.finditer(span)]
        commands.extend(name for name in command_names if name in _SEMANTIC_LATEX_COMMANDS)
        without_environment_names = _LATEX_ENV_RE.sub(" ", span)
        without_commands = _LATEX_COMMAND_RE.sub(" ", without_environment_names)
        # 单纯比较可见的拉丁字母/数字序列，避免把 \frac、环境名等排版
        # 命令误认为内容；括号和空格属于可安全重排的 TeX 表达方式。
        literals.extend(re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?", without_commands))
    return tuple(literals), tuple(commands)


def _exercise_signatures(markdown: str) -> list[str]:
    return [m.group(1) for m in _EXERCISE_START_RE.finditer(markdown)]


def _answer_marker_counts_by_exercise(markdown: str) -> list[int]:
    """返回每道题内部的解/答标记数，防止模型跨题新增或挪动答案。"""
    starts = list(_EXERCISE_START_RE.finditer(markdown))
    counts: list[int] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        counts.append(len(_ANSWER_MARKER_RE.findall(markdown[start.start() : end])))
    return counts


def _answer_marker_kinds_by_exercise(markdown: str) -> list[list[str]]:
    """返回每道题内部按顺序出现的解/答标记类型。"""
    starts = list(_EXERCISE_START_RE.finditer(markdown))
    kinds: list[list[str]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(markdown)
        kinds.append(
            [match.group(1) for match in _ANSWER_MARKER_RE.finditer(markdown[start.start() : end])]
        )
    return kinds


def _rebuild_parenthesized_question_answers(source: str) -> str | None:
    """从编号明确的 OCR 题干和答案区构造可再次切分的整节 Markdown。"""
    markers = list(_ANSWER_MARKER_RE.finditer(source))
    if len(markers) != 1:
        return None
    marker = markers[0]
    questions = source[: marker.start()]
    answers = source[marker.end() :]
    question_starts = list(_NUMBERED_ANSWER_START_RE.finditer(questions))
    answer_starts = list(_NUMBERED_ANSWER_START_RE.finditer(answers))
    question_numbers = [match.group(1) for match in question_starts]
    answer_numbers = [match.group(1) for match in answer_starts]
    if (
        len(question_numbers) < 2
        or question_numbers != answer_numbers
        or question_numbers != [str(index) for index in range(1, len(question_numbers) + 1)]
    ):
        return None

    def chunks(text: str, starts: list[re.Match[str]]) -> list[str]:
        return [
            text[match.start() : starts[index + 1].start() if index + 1 < len(starts) else len(text)].strip()
            for index, match in enumerate(starts)
        ]

    question_chunks = chunks(questions, question_starts)
    answer_chunks = chunks(answers, answer_starts)
    result = []
    for index, (question, answer) in enumerate(zip(question_chunks, answer_chunks)):
        question_label = question_starts[index]
        question_body = question[question_label.end() - question_label.start() :].strip()
        if not question_body or not answer:
            return None
        # 试卷答题区末尾常附有“得分/评阅人”表格，不属于最后一题答案。
        answer = re.sub(
            r"(?is)<table\b[^>]*>.*?</table>",
            lambda match: "" if "得分" in match.group() and "评阅人" in match.group() else match.group(),
            answer,
        ).strip()
        result.append(
            f"*{question_numbers[index]}.* {question_body}\n\n**解**: {answer}"
        )
    return "\n\n".join(result).rstrip() + "\n"


def _remove_unexpected_answer_segments(
    original: str, revised: str, *, allow_relocation: bool = False
) -> tuple[str, int]:
    """保护原有解答，删除模型新增的解答段。

    默认按原题位置保护答案，供单题审校和数学审校使用。整节基础审校可启用
    ``allow_relocation``：答案块必须能与原节中的某个已有答案匹配，匹配后恢复
    原答案文本，允许它跨题移动但不允许模型改写或新增。
    """
    if allow_relocation:
        def compact_with_offsets(value: str) -> tuple[str, list[int]]:
            offsets = [index for index, char in enumerate(value) if not char.isspace()]
            return "".join(value[index] for index in offsets), offsets

        source_blocks = []
        original_markers = list(_ANSWER_MARKER_RE.finditer(original))
        original_exercises = list(_EXERCISE_START_RE.finditer(original))
        original_headings = list(_HEADING_RE.finditer(original))
        for marker_index, marker in enumerate(original_markers):
            boundaries = [
                item.start() for item in original_markers[marker_index + 1 :]
            ] + [item.start() for item in original_exercises if item.start() > marker.end()]
            boundaries.extend(
                item.start() for item in original_headings if item.start() > marker.end()
            )
            end = min(boundaries) if boundaries else len(original)
            body = original[marker.end() : end].strip()
            if body:
                numbered_starts = list(_NUMBERED_ANSWER_START_RE.finditer(body))
                if len(numbered_starts) > 1 and len(original_exercises) > 1:
                    chunks = [
                        (
                            numbered_starts[index].start(),
                            numbered_starts[index + 1].start()
                            if index + 1 < len(numbered_starts)
                            else len(body),
                            numbered_starts[index].group(1),
                        )
                        for index in range(len(numbered_starts))
                    ]
                else:
                    chunks = [(0, len(body), None)]
                for chunk_start, chunk_end, number in chunks:
                    answer_body = body[chunk_start:chunk_end].strip()
                    compact, offsets = compact_with_offsets(answer_body)
                    source_blocks.append(
                        {
                            "marker": marker.group(0),
                            "body": answer_body,
                            "compact": compact,
                            "offsets": offsets,
                            "number": number,
                            "used": set(),
                        }
                    )

        revised_markers = list(_ANSWER_MARKER_RE.finditer(revised))
        revised_exercises = list(_EXERCISE_START_RE.finditer(revised))
        revised_headings = list(_HEADING_RE.finditer(revised))
        replacements: list[tuple[int, int, str]] = []
        removed = 0
        for marker_index, marker in enumerate(revised_markers):
            boundaries = [
                item.start() for item in revised_markers[marker_index + 1 :]
            ] + [item.start() for item in revised_exercises if item.start() > marker.end()]
            boundaries.extend(
                item.start() for item in revised_headings if item.start() > marker.end()
            )
            end = min(boundaries) if boundaries else len(revised)
            candidate = revised[marker.end() : end].strip()
            compact_candidate, _ = compact_with_offsets(candidate)
            best: tuple[float, dict[str, Any], int, int] | None = None
            current_exercise = next(
                (
                    item
                    for item in reversed(revised_exercises)
                    if item.start() < marker.start()
                ),
                None,
            )
            current_number = current_exercise.group(1) if current_exercise else None
            if current_number is not None:
                for source_block in source_blocks:
                    if (
                        source_block["number"] == current_number
                        and not source_block["used"]
                        and source_block["compact"]
                    ):
                        best = (
                            1.0,
                            source_block,
                            0,
                            len(source_block["compact"]),
                        )
                        break
            for source_block in source_blocks:
                if best is not None:
                    break
                compact_source = source_block["compact"]
                if not compact_source or not compact_candidate:
                    continue
                cursor = compact_source.find(compact_candidate)
                while cursor >= 0:
                    matched_range = range(cursor, cursor + len(compact_candidate))
                    if not any(index in source_block["used"] for index in matched_range):
                        best = (1.0, source_block, cursor, cursor + len(compact_candidate))
                        break
                    cursor = compact_source.find(compact_candidate, cursor + 1)

                if best is not None:
                    break

                # Allow light OCR punctuation/spacing edits only when the entire
                # source answer block is still unused; restore that original block.
                if not source_block["used"]:
                    similarity = SequenceMatcher(
                        None, compact_source, compact_candidate
                    ).ratio()
                    if (
                        compact_source in compact_candidate
                        or similarity >= 0.72
                    ) and (
                        best is None or similarity > best[0]
                    ):
                        best = (
                            similarity,
                            source_block,
                            0,
                            len(compact_source),
                        )
            if best is None:
                replacements.append((marker.start(), end, ""))
                removed += 1
            else:
                _, source_block, compact_start, compact_end = best
                source_block["used"].update(range(compact_start, compact_end))
                offsets = source_block["offsets"]
                body_start = offsets[compact_start]
                body_end = offsets[compact_end - 1] + 1
                matched_body = source_block["body"][body_start:body_end].strip()
                replacements.append(
                    (marker.start(), end, f"{source_block['marker']}{matched_body}\n\n")
                )
        for start, end, replacement in reversed(replacements):
            revised = revised[:start] + replacement + revised[end:]
        if any(
            len(source_block["used"]) != len(source_block["compact"])
            for source_block in source_blocks
        ):
            raise ValueError(
                "LLM 审校结果遗漏了原有解答，拒绝返回不完整章节"
            )
        return revised, removed

    original_numbers = _exercise_signatures(original)
    revised_numbers = _exercise_signatures(revised)
    if original_numbers != revised_numbers:
        return revised, 0
    original_kinds = _answer_marker_kinds_by_exercise(original)
    original_segments = _exercise_segments(original)
    revised_starts = list(_EXERCISE_START_RE.finditer(revised))
    if len(original_kinds) != len(revised_starts):
        return revised, 0

    replacements: list[tuple[int, int, str]] = []
    removed = 0
    for index, start in enumerate(revised_starts):
        end = (
            revised_starts[index + 1].start()
            if index + 1 < len(revised_starts)
            else len(revised)
        )
        segment = revised[start.start() : end]
        markers = list(_ANSWER_MARKER_RE.finditer(segment))
        expected = original_kinds[index]
        unexpected_indexes: set[int] = set()
        expected_index = 0
        for marker_index, marker in enumerate(markers):
            if expected_index < len(expected) and marker.group(1) == expected[expected_index]:
                expected_index += 1
            else:
                next_marker = markers[marker_index + 1] if marker_index + 1 < len(markers) else None
                candidate = segment[marker.end() : next_marker.start() if next_marker else len(segment)].strip()
                source = original_segments[index]
                unmarked = re.search(r"(?m)^\s*解(?=[^\s：:.．*])", source)
                source_body = source[unmarked.end() :].strip() if unmarked else ""
                compact_candidate = re.sub(r"\s+", "", candidate)
                compact_source = re.sub(r"\s+", "", source_body)
                existing_solution = bool(
                    marker.group(1) in ("解", "解答", "解析", "详解")
                    and compact_candidate
                    and compact_source
                    and (
                        compact_candidate in compact_source
                        or (
                            len(compact_candidate) >= 40
                            and len(compact_candidate) <= len(compact_source) * 1.02
                            and SequenceMatcher(None, compact_source, compact_candidate).ratio() >= 0.9
                        )
                    )
                )
                if not existing_solution:
                    unexpected_indexes.add(marker_index)
        # 原有标记没有按顺序保留下来时，不能确定哪些文本是模型新增的。
        if expected_index != len(expected):
            continue
        # 从每个新增标记起删除到下一个解/答标记；最后一个则删到本题末尾。
        pieces: list[str] = []
        cursor = 0
        for marker_index, marker in enumerate(markers):
            if marker_index not in unexpected_indexes:
                continue
            pieces.append(segment[cursor : marker.start()].rstrip())
            next_marker = (
                markers[marker_index + 1]
                if marker_index + 1 < len(markers)
                else None
            )
            if next_marker is None:
                # 保留答案段之后可能出现的下一个小节标题，避免清理答案时吞掉标题。
                tail = segment[marker.start() :]
                heading = _HEADING_RE.search(tail)
                if heading is not None:
                    pieces.append("\n\n" + tail[heading.start() :].lstrip())
                else:
                    pieces.append("\n\n")
                cursor = len(segment)
            else:
                pieces.append("\n\n")
                cursor = next_marker.start()
            removed += 1
        pieces.append(segment[cursor:])
        cleaned = "".join(pieces)
        replacements.append((start.start(), start.start() + len(segment), cleaned))

    for start, end, replacement in reversed(replacements):
        revised = revised[:start] + replacement + revised[end:]
    return revised, removed


def _math_literals_are_preserved(original: str, revised: str) -> bool:
    """基础审校不得猜测数学字母、数字或函数命令。

    这是故意偏保守的防线：一旦某题的数学字面序列发生变化，就保留
    OCR 原文交给后续流程或人工处理，而不是把模型的猜测
    写回结果。
    """
    original_segments = _exercise_segments(original)
    revised_segments = _exercise_segments(revised)
    if len(original_segments) != len(revised_segments):
        return False
    return all(
        _math_literal_signature(source) == _math_literal_signature(candidate)
        for source, candidate in zip(original_segments, revised_segments)
    )


def _structure_is_preserved(
    original: str, revised: str, *, check_math_literals: bool = False
) -> bool:
    """只允许修改题目文本，不允许模型增删/重排题目或解答段。"""
    preserved = (
        _exercise_signatures(original) == _exercise_signatures(revised)
        and _answer_marker_kinds_by_exercise(original)
        == _answer_marker_kinds_by_exercise(revised)
    )
    if preserved and check_math_literals:
        preserved = _math_literals_are_preserved(original, revised)
    return preserved


def _log_structure_mismatch(stage: str, original: str, revised: str) -> None:
    """记录结构校验差异，但不记录题目正文，避免日志过大或泄露内容。"""
    original_numbers = _exercise_signatures(original)
    revised_numbers = _exercise_signatures(revised)
    original_answers = _answer_marker_kinds_by_exercise(original)
    revised_answers = _answer_marker_kinds_by_exercise(revised)
    logger.warning(
        "%s结构校验差异：原题号=%s，返回题号=%s，原每题解/答序列=%s，返回=%s",
        stage,
        original_numbers,
        revised_numbers,
        original_answers,
        revised_answers,
    )
    if (
        original_numbers == revised_numbers
        and original_answers == revised_answers
        and not _math_literals_are_preserved(original, revised)
    ):
        logger.warning("%s数学字面量校验失败：疑似模型猜测或重写了公式内容", stage)


def _review_text_similarity(original: str, revised: str) -> float:
    """计算去除空白后的文本相似度，仅用于风险日志，不阻断结果。"""
    source = re.sub(r"\s+", "", original)
    candidate = re.sub(r"\s+", "", revised)
    if not source and not candidate:
        return 1.0
    return SequenceMatcher(None, source, candidate).ratio()


def _log_review_quality(
    original: str,
    revised: str,
    document_name: str,
    section_name: str,
) -> None:
    """记录题数、补答比例和文本相似度风险，绝不回退模型结果。"""
    original_count = len(_exercise_signatures(original))
    revised_count = len(_exercise_signatures(revised))
    if original_count != revised_count:
        logger.warning(
            "基础审校题数变化（严重）：文档=%s，小节=%s，原题数=%d，审校后=%d；仍采用模型结果",
            document_name,
            section_name,
            original_count,
            revised_count,
        )

    original_answer_count = len(_ANSWER_MARKER_RE.findall(original))
    revised_answer_count = len(_ANSWER_MARKER_RE.findall(revised))
    added_answers = max(0, revised_answer_count - original_answer_count)
    if added_answers:
        ratio = added_answers / max(original_count, 1)
        level = "可接受范围内" if ratio <= 0.10 else "超过10%需重点检查"
        logger.warning(
            "基础审校新增解/答标记：文档=%s，小节=%s，新增=%d，原题数=%d，占比=%.1f%%（%s）；仍采用模型结果",
            document_name,
            section_name,
            added_answers,
            original_count,
            ratio * 100,
            level,
        )

    similarity = _review_text_similarity(original, revised)
    if similarity < MIN_REVIEW_TEXT_SIMILARITY:
        logger.warning(
            "基础审校文本相似度过低：文档=%s，小节=%s，相似度=%.3f，阈值=%.3f；仍采用模型结果",
            document_name,
            section_name,
            similarity,
            MIN_REVIEW_TEXT_SIMILARITY,
        )
    else:
        logger.info(
            "基础审校质量指标：文档=%s，小节=%s，原题数=%d，审校后题数=%d，文本相似度=%.3f",
            document_name,
            section_name,
            original_count,
            revised_count,
            similarity,
        )


def _parse_json_response(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _message_content(response: Any) -> str | None:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return None
    message = getattr(choices[0], "message", None)
    if message is None and isinstance(choices[0], dict):
        message = choices[0].get("message")
    if isinstance(message, dict):
        return message.get("content")
    return getattr(message, "content", None)


def _make_client(
    client: Any,
    base_url: str | None,
    api_key: str | None,
    timeout_seconds: int = DEFAULT_REVIEW_TIMEOUT_SECONDS,
) -> Any:
    if client is not None:
        return client
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise RuntimeError("审校接口需要安装 openai：pip install openai") from exc
    resolved_key = (
        api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    if not resolved_key:
        raise ValueError("需要 api_key，或设置环境变量 DEEPSEEK_API_KEY")
    kwargs: dict[str, Any] = {
        "api_key": resolved_key,
        "timeout": timeout_seconds,
        "max_retries": 0,
    }
    kwargs["base_url"] = (
        base_url or os.environ.get("OPENAI_BASE_URL") or DEFAULT_REVIEW_BASE_URL
    )
    return OpenAI(**kwargs)


def _chat_json(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    reasoning_effort: str = DEFAULT_REVIEW_REASONING_EFFORT,
) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    # 仅供暂存的 math review 使用；basic review 走下方的纯文本调用。
    if not tools:
        kwargs["response_format"] = {"type": "json_object"}
    if reasoning_effort != "none":
        kwargs["reasoning_effort"] = reasoning_effort
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    try:
        return client.chat.completions.create(**kwargs)
    except TypeError:
        # 一些旧版 OpenAI-compatible 服务不接受 response_format。
        kwargs.pop("response_format", None)
        return client.chat.completions.create(**kwargs)


def _chat_text(
    client: Any,
    model: str,
    messages: list[dict[str, Any]],
    reasoning_effort: str = DEFAULT_REVIEW_REASONING_EFFORT,
) -> Any:
    """调用不带结构化输出约束的文本接口，供 basic review 使用。"""
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if reasoning_effort != "none":
        kwargs["reasoning_effort"] = reasoning_effort
    else:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    return client.chat.completions.create(**kwargs)


def _structured_exercises(value: dict[str, Any] | None) -> list[dict[str, str]] | None:
    """校验模型的四字段结果，避免把模型说明文字当作题干。"""
    from ..parse_md_exercises import (
        ANSWER_START_RE,
        PROOF_START_RE,
        SOLUTION_START_RE,
        normalize_solution_method_labels,
        split_at_line_marker,
        split_solution_method_block,
    )

    canonical_proof_marker = re.compile(r"^\s*\*\*\s*(?:证明|证)\s*\*\*[：:]?")

    def clean(text: str) -> str:
        text = re.sub(r"(?m)^\s*<!--\s*page:\s*\d+\s*-->\s*$", "", text)
        text = re.sub(
            r"(?is)<table\b[^>]*>.*?</table>",
            lambda match: "" if "得分" in match.group() and "评阅人" in match.group() else match.group(),
            text,
        )
        return text.strip()

    raw_items = value.get("exercises") if isinstance(value, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        return None
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            return None
        number = str(raw_item.get("number") or "").strip()
        if not re.fullmatch(r"\d{1,3}", number) or number in seen:
            return None
        question = raw_item.get("question")
        solution = raw_item.get("solution_steps", "")
        answer = raw_item.get("answer", "")
        if not isinstance(question, str) or not question.strip():
            return None
        if not isinstance(solution, str) or not isinstance(answer, str):
            return None
        seen.add(number)
        question = clean(re.sub(
            rf"^\s*(?:\*{{0,2}}{number}\s*[.．、]\*{{0,2}}|[（(]{number}[）)])\s*",
            "",
            question,
            count=1,
        ))
        solution = clean(normalize_solution_method_labels(solution))
        answer = clean(re.sub(r"^\s*(?:\*\*\s*答\s*\*\*|答案|答)\s*[：:]\s*", "", answer))
        question, misplaced_methods = split_solution_method_block(question)
        solution_kind = "proof" if PROOF_START_RE.match(solution) else None
        if solution_kind:
            solution = PROOF_START_RE.sub("", solution, count=1).strip()
        elif SOLUTION_START_RE.match(solution):
            solution = SOLUTION_START_RE.sub("", solution, count=1).strip()
            if PROOF_START_RE.match(solution):
                solution_kind = "proof"
                solution = PROOF_START_RE.sub("", solution, count=1).strip()
        solution = normalize_solution_method_labels(solution)
        question, misplaced_proof = split_at_line_marker(
            question, canonical_proof_marker, allow_first_line=False
        )
        question, misplaced_solution = split_at_line_marker(
            question, SOLUTION_START_RE, allow_first_line=False
        )
        if misplaced_proof:
            solution_kind = "proof"
        misplaced = normalize_solution_method_labels(
            "\n\n".join(
                part for part in (misplaced_proof, misplaced_methods, misplaced_solution) if part
            )
        )
        if misplaced:
            if PROOF_START_RE.match(misplaced):
                solution_kind = "proof"
                misplaced = PROOF_START_RE.sub("", misplaced, count=1).strip()
            solution = clean("\n\n".join(part for part in (misplaced, solution) if part))
        question, misplaced_answer = split_at_line_marker(question, ANSWER_START_RE)
        if misplaced_answer and not answer:
            answer = clean(misplaced_answer)
        solution, misplaced_answer = split_at_line_marker(solution, ANSWER_START_RE)
        if misplaced_answer and not answer:
            answer = clean(misplaced_answer)
        question = _normalize_math_delimiters(question)
        solution = _normalize_math_delimiters(solution)
        answer = _normalize_math_delimiters(answer)
        item = {
            "number": number,
            "question": question,
            "solution_steps": solution,
            "answer": answer,
        }
        if solution_kind:
            item["_solution_kind"] = solution_kind
        items.append(item)
    return items


_MATH_ONLY_CHARS_RE = re.compile(r"^[A-Za-z0-9\\{}_^.,()+\-*/=<>|:;!%\s]+$")
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_UNBRACED_ATOP_FRACTION_RE = re.compile(
    r"(?P<relation>\\(?:leqslant|geqslant|less|greater|approx|sim)|=)"
    r"(?P<space>\s*)(?!\{)(?P<numerator>[^,;&]+?)"
    r"\\atop\s*(?P<denominator>\{[^{}]+\}|[A-Za-z0-9]+)"
)


def _normalize_math_delimiters(value: str) -> str:
    """Convert common TeX delimiters and wrap bare formula-only Markdown fields."""
    value = re.sub(r"\\displaylimits(?![A-Za-z])", r"\\limits", value)
    value = re.sub(r"\\begin\{align\*?\}", r"\\begin{aligned}", value)
    value = re.sub(r"\\end\{align\*?\}", r"\\end{aligned}", value)

    def replace_unbraced_atop(match: re.Match[str]) -> str:
        numerator = match.group("numerator").strip()
        denominator = match.group("denominator").strip()
        return (
            f"{match.group('relation')}{match.group('space')}"
            f"\\frac{{{numerator}}}{{{denominator}}}"
        )

    value = _UNBRACED_ATOP_FRACTION_RE.sub(replace_unbraced_atop, value)
    value = re.sub(r"\\\((.*?)\\\)", r"$\1$", value, flags=re.DOTALL)
    value = re.sub(
        r"\\\[(.*?)\\\]",
        lambda match: "\n\n$$" + match.group(1).strip("\n") + "$$\n\n",
        value,
        flags=re.DOTALL,
    )

    def standalone_display_math(match: re.Match[str]) -> str:
        formula = match.group(1).strip()
        if formula.startswith(r"\begin{"):
            formula = "\n" + formula
            formula = re.sub(r"(\\end\{[^{}]+\})\s*$", r"\1\n", formula)
        return "\n\n$$" + formula + "$$\n\n"

    value = re.sub(
        r"(?<!\$)\$\$(.+?)\$\$(?!\$)",
        standalone_display_math,
        value,
        flags=re.DOTALL,
    )
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip("\n")
    stripped = value.strip()
    if (
        stripped
        and not re.search(r"\$", stripped)
        and _MATH_ONLY_CHARS_RE.fullmatch(stripped)
        and (
            _LATEX_COMMAND_RE.search(stripped)
            or re.search(r"[=^_<>+*/]", stripped)
        )
    ):
        delimiter = "$$" if "\n" in stripped or r"\begin{" in stripped else "$"
        return delimiter + stripped + delimiter
    return value


def _structured_review_stage(
    llm: Any,
    *,
    model: str,
    prompt: str,
    operation: str,
    usage_callback: UsageCallback | None,
    reasoning_effort: str,
) -> list[dict[str, str]] | None:
    response = _chat_json(
        llm,
        model,
        [
            {"role": "system", "content": "只返回合法 JSON 对象，完整保留数学题目与已有解答。"},
            {"role": "user", "content": prompt},
        ],
        reasoning_effort=reasoning_effort,
    )
    report_usage(
        usage_callback,
        usage_from_response(response, operation=operation, model=model),
    )
    return _structured_exercises(_parse_json_response(_message_content(response)))


def _is_proof_question(text: str) -> bool:
    return bool(re.search(r"(?:试证|证明|prove\b|show\s+that\b)", text, re.IGNORECASE))


def _is_concise_answer_only(text: str) -> bool:
    """是否像单独给出的简短结果，而不是解题/证明过程。"""
    value = re.sub(r"(?s)\$\$?.*?\$\$?", " MATH ", text or "").strip()
    if not value or len(value) > 120 or "\n" in value:
        return False
    reasoning = re.compile(
        r"因为|所以|因此|故|由.{0,12}(?:得|可知|可得)|根据|设|令|解得|计算得|可证|证明|"
        r"because|therefore|thus|hence|since|we\s+have|it\s+follows|proof",
        re.IGNORECASE,
    )
    return not reasoning.search(value)


def _solution_was_truncated(original: str, revised: str) -> bool:
    """Detect substantial omissions from a single-exercise review response."""
    source = (original or "").strip()
    candidate = (revised or "").strip()
    if not source:
        return False
    if not candidate:
        return True
    source_parts = re.findall(r"(?m)^[ \t]*(?:解\s*)?[（(](\d{1,3})[）)]", source)
    revised_parts = set(
        re.findall(r"(?m)^[ \t]*(?:解\s*)?[（(](\d{1,3})[）)]", candidate)
    )
    if len(source_parts) > 1 and any(part not in revised_parts for part in source_parts):
        return True
    source_size = len(re.sub(r"\s+", "", source))
    revised_size = len(re.sub(r"\s+", "", candidate))
    return source_size >= 120 and revised_size < source_size * 0.55


_UNMARKED_SOLUTION_START_RE = re.compile(
    r"^\s*解(?=由于|因为|根据|由|设|令|先|利用|从|当|对|将|易得|可得|注意|已知)"
)
_POTENTIAL_UNMARKED_SOLUTION_RE = re.compile(
    r"(?m)^[ \t]*解(?!答|析|决|释|题)(?=[\u4e00-\u9fff])"
)
_POTENTIAL_MATH_SOLUTION_RE = re.compile(
    r"(?s)(?:\$\$|\\\[)\s*\\begin\{(?:aligned|align\*?)\}\s*&?\s*解(?=\s|\\(?:quad|qquad))"
)


def _has_potential_unmarked_solution(content: str) -> bool:
    return bool(
        _POTENTIAL_UNMARKED_SOLUTION_RE.search(content)
        or _POTENTIAL_MATH_SOLUTION_RE.search(content)
    )


def _split_embedded_solution(content: str) -> tuple[str, str]:
    """Recover an OCR solution introduced by an unpunctuated line like '解由于'."""
    lines = content.splitlines()
    first_nonblank = next((i for i, line in enumerate(lines) if line.strip()), None)
    for index, line in enumerate(lines):
        if index == first_nonblank:
            continue
        marker = _UNMARKED_SOLUTION_START_RE.match(line)
        if marker:
            question = "\n".join(lines[:index]).strip()
            solution = (line[marker.end():] + "\n" + "\n".join(lines[index + 1:])).strip()
            if question and solution:
                return question, solution
    return content.strip(), ""


def _render_structured_exercises(
    items: list[dict[str, str]],
    baseline_items: list[dict[str, Any]] | None = None,
) -> str:
    from ..parse_md_exercises import normalize_solution_method_labels

    baseline_by_number = {
        str(item.get("number") or ""): item for item in (baseline_items or [])
    }
    chunks = []
    for item in items:
        chunk = f"*{item['number']}.* {item['question']}"
        if item["solution_steps"]:
            solution = normalize_solution_method_labels(item["solution_steps"]).strip()
            baseline = baseline_by_number.get(item["number"], {})
            solution_label = "证" if (
                item.get("_solution_kind") == "proof"
                or baseline.get("question_type") == "proof"
                or _is_proof_question(item["question"])
            ) else "解"
            if solution.startswith("$$"):
                chunk += f"\n\n**{solution_label}**:\n\n{solution}"
            else:
                chunk += f"\n\n**{solution_label}**: {solution}"
        if item["answer"]:
            answer = item["answer"].strip()
            if answer.startswith("$$"):
                chunk += f"\n\n**答**:\n\n{answer}"
            else:
                chunk += f"\n\n**答**: {answer}"
        chunks.append(chunk)
    return "\n\n".join(chunks).rstrip() + "\n"


def _review_raw_source_two_pass(
    source: str,
    document_name: str,
    section_name: str,
    *,
    baseline_items: list[dict[str, Any]],
    base_url: str | None,
    api_key: str | None,
    model: str,
    timeout_seconds: int,
    reasoning_effort: str,
    client: Any,
    usage_callback: UsageCallback | None,
    reference_source: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """先匹配，再校对；最终 Markdown 由四字段结构统一渲染。"""
    from ..parse_md_exercises import SOLUTION_START_RE, split_at_line_marker, split_exercises

    llm = _make_client(client, base_url, api_key, timeout_seconds)
    fallback_markdown = _rebuild_parenthesized_question_answers(source)
    fallback_items: list[dict[str, str]] = []
    if fallback_markdown:
        fallback_items = [
            {
                "number": item["number"],
                "question": item["content"],
                "solution_steps": item.get("solution_steps") or "",
                "answer": item.get("answer") or "",
            }
            for item in split_exercises(fallback_markdown, check_section_end=False)
            if item.get("number") != "__preamble__"
        ]
    if not fallback_items:
        fallback_items = [
            {
                "number": str(item.get("number") or ""),
                "question": str(item.get("content") or ""),
                "solution_steps": str(item.get("solution_steps") or ""),
                "answer": str(item.get("answer") or ""),
            }
            for item in split_exercises(source, check_section_end=False)
            if item.get("number") != "__preamble__"
        ]
    if not fallback_items and len(baseline_items) == 1:
        _, raw_solution = split_at_line_marker(
            source, SOLUTION_START_RE, allow_first_line=False
        )
        if raw_solution:
            original = baseline_items[0]
            fallback_items = _structured_exercises({"exercises": [{
                "number": str(original.get("number") or ""),
                "question": str(original.get("content") or ""),
                "solution_steps": raw_solution,
                "answer": str(original.get("answer") or ""),
            }]}) or []
    recovered_items = _recover_escaped_numbered_exercises(
        source, [item["number"] for item in fallback_items]
    )
    if recovered_items:
        fallback_items = recovered_items
    expected_numbers = [item["number"] for item in fallback_items]
    baseline_by_number = {
        str(item.get("number") or ""): item
        for item in baseline_items
        if item.get("number")
    }
    for item in fallback_items:
        baseline = None if recovered_items else baseline_by_number.get(item["number"])
        if baseline is None:
            continue
        for field in ("solution_steps", "answer"):
            if not item[field] and baseline.get(field):
                item[field] = str(baseline[field])
    reference_by_number: dict[str, dict[str, Any]] = {}
    if reference_source and not recovered_items:
        reference_fallback = _rebuild_parenthesized_question_answers(reference_source)
        reference_items = [
            item
            for item in split_exercises(
                reference_fallback or reference_source,
                check_section_end=False,
            )
            if item.get("number") != "__preamble__"
        ]
        reference_by_number = {
            str(item.get("number") or ""): item for item in reference_items
        }
        if len(baseline_items) == 1:
            _, raw_reference_solution = split_at_line_marker(
                reference_source, SOLUTION_START_RE, allow_first_line=False
            )
            if raw_reference_solution:
                number = str(baseline_items[0].get("number") or "")
                reference_item = reference_by_number.setdefault(
                    number,
                    {
                        "number": number,
                        "content": str(baseline_items[0].get("content") or ""),
                        "solution_steps": "",
                        "answer": "",
                    },
                )
                if not reference_item.get("solution_steps"):
                    reference_item["solution_steps"] = raw_reference_solution
        for item in fallback_items:
            reference_item = reference_by_number.get(item["number"])
            if reference_item is None:
                continue
            for field in ("solution_steps", "answer"):
                if not item[field] and reference_item.get(field):
                    item[field] = str(reference_item[field])
    if not fallback_items:
        fallback_items = [
            {
                "number": str(item.get("number") or ""),
                "question": str(item.get("content") or ""),
                "solution_steps": str(item.get("solution_steps") or ""),
                "answer": str(item.get("answer") or ""),
            }
            for item in baseline_items
            if item.get("number") and item.get("content")
        ]
    for item in fallback_items:
        reference_item = reference_by_number.get(item["number"])
        if reference_item is None:
            continue
        for field in ("solution_steps", "answer"):
            if not item[field] and reference_item.get(field):
                item[field] = str(reference_item[field])
    fallback_by_number = {item["number"]: item for item in fallback_items}
    match_prompt = (
        MATCH_ANSWERS_PROMPT_TEMPLATE.replace("{document_name}", document_name)
        .replace("{section_name}", section_name)
        .replace("{draft}", source)
        .replace("{reference}", reference_source or source)
    )
    matched = _structured_review_stage(
        llm,
        model=model,
        prompt=match_prompt,
        operation="review.match",
        usage_callback=usage_callback,
        reasoning_effort=reasoning_effort,
    )
    warnings: list[dict[str, Any]] = []
    if matched is None or (expected_numbers and [item["number"] for item in matched] != expected_numbers):
        if not fallback_items:
            raise ValueError("第一轮审校未返回可解析的题目与答案匹配结果")
        matched = fallback_items
        warnings.append({"code": "parenthesized_ocr_fallback"})
    for item in matched:
        source_item = fallback_by_number.get(item["number"])
        reference_item = reference_by_number.get(item["number"])
        if source_item and reference_item:
            for field in ("question", "solution_steps", "answer"):
                draft_value = str(
                    source_item.get("question") if field == "question" else source_item.get(field) or ""
                )
                reference_value = str(
                    reference_item.get("content")
                    if field == "question"
                    else reference_item.get(field) or ""
                )
                if (
                    draft_value
                    and reference_value
                    and draft_value != reference_value
                    and item[field] == reference_value
                ):
                    item[field] = draft_value
        baseline_item = None if recovered_items else baseline_by_number.get(item["number"])
        if baseline_item and reference_source:
            for field in ("question", "solution_steps", "answer"):
                draft_value = str(
                    baseline_item.get("content")
                    if field == "question"
                    else baseline_item.get(field) or ""
                )
                if (
                    draft_value
                    and item[field] != draft_value
                    and item[field] in reference_source
                ):
                    item[field] = draft_value
        if source_item and source_item["solution_steps"] and (
            not item["solution_steps"]
            or len(item["solution_steps"]) < len(source_item["solution_steps"]) * 0.5
        ):
            item["solution_steps"] = source_item["solution_steps"]

    # Keep the answer field evidence-based even if either LLM pass invents one.
    # A short result that was the only material after the question is an answer;
    # a derivation or proof remains solely in solution_steps, including its last line.
    for item in matched:
        source_item = fallback_by_number.get(item["number"], {})
        reference_item = reference_by_number.get(item["number"], {})
        explicit_answer = bool(
            source_item.get("answer") or reference_item.get("answer")
        )
        concise_only = next(
            (
                str(candidate.get("solution_steps") or "").strip()
                for candidate in (source_item, reference_item)
                if _is_concise_answer_only(str(candidate.get("solution_steps") or ""))
            ),
            None,
        )
        if not explicit_answer and not concise_only:
            item["answer"] = ""

    format_prompt = (
        FORMAT_ANSWERS_PROMPT_TEMPLATE.replace("{document_name}", document_name)
        .replace("{section_name}", section_name)
        .replace(
            "{content}",
            json.dumps(
                {"exercises": [
                    {key: item[key] for key in ("number", "question", "solution_steps", "answer")}
                    for item in matched
                ]},
                ensure_ascii=False,
            ),
        )
        .replace("{draft}", source)
        .replace("{reference}", reference_source or source)
    )
    formatted = _structured_review_stage(
        llm,
        model=model,
        prompt=format_prompt,
        operation="review.format",
        usage_callback=usage_callback,
        reasoning_effort=reasoning_effort,
    )
    if formatted is None or [item["number"] for item in formatted] != [item["number"] for item in matched]:
        formatted = matched
        warnings.append({"code": "format_review_fallback"})
    else:
        for item, previous in zip(formatted, matched):
            if previous.get("_solution_kind") == "proof":
                item["_solution_kind"] = "proof"
            for field in ("question", "solution_steps", "answer"):
                if not item[field] or (
                    field == "solution_steps"
                    and previous[field]
                    and len(item[field]) < len(previous[field]) * 0.5
                ):
                    item[field] = previous[field]
            source_item = fallback_by_number.get(item["number"])
            reference_item = reference_by_number.get(item["number"])
            if source_item and reference_item:
                for field in ("question", "solution_steps", "answer"):
                    draft_value = str(
                        source_item.get("question") if field == "question" else source_item.get(field) or ""
                    )
                    reference_value = str(
                        reference_item.get("content")
                        if field == "question"
                        else reference_item.get(field) or ""
                    )
                    if (
                        draft_value
                        and reference_value
                        and draft_value != reference_value
                        and item[field] == reference_value
                    ):
                        item[field] = draft_value
            baseline_item = baseline_by_number.get(item["number"])
            if baseline_item and reference_source:
                for field in ("question", "solution_steps", "answer"):
                    draft_value = str(
                        baseline_item.get("content")
                        if field == "question"
                        else baseline_item.get(field) or ""
                    )
                    if (
                        draft_value
                        and item[field] != draft_value
                        and item[field] in reference_source
                    ):
                        item[field] = draft_value
    # The formatting pass is also not allowed to turn a derivation into a new
    # answer field. Reapply the source-evidence rule after it has completed.
    for item in formatted:
        source_item = fallback_by_number.get(item["number"], {})
        reference_item = reference_by_number.get(item["number"], {})
        if source_item.get("answer") or reference_item.get("answer"):
            continue
        concise_only = next(
            (
                str(candidate.get("solution_steps") or "").strip()
                for candidate in (source_item, reference_item)
                if _is_concise_answer_only(str(candidate.get("solution_steps") or ""))
            ),
            None,
        )
        if not concise_only:
            item["answer"] = ""
    logger.info(
        "整节两轮审校完成：文档=%s，小节=%s，题数=%d，缺少最终答案=%d",
        document_name,
        section_name,
        len(formatted),
        sum(not item["answer"] for item in formatted),
    )
    return _render_structured_exercises(formatted, baseline_items), warnings


def review_markdown_basic(
    content: str,
    document_name: str,
    section_name: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    timeout_seconds: int = DEFAULT_REVIEW_TIMEOUT_SECONDS,
    reasoning_effort: str = DEFAULT_REVIEW_REASONING_EFFORT,
    client: Any = None,
    usage_callback: UsageCallback | None = None,
) -> str:
    """按模板对整节执行基础 OCR/标点/LaTeX 审校。

    服务异常或没有返回 Markdown 时直接抛出异常；模型必须返回模板要求的纯 Markdown，
    不要求模型或客户端追加逐题状态。
    client 可注入 OpenAI-compatible fake 供测试使用。
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("content 必须是非空 Markdown 字符串")
    exercise_count = len(_exercise_signatures(content))
    logger.info(
        "基础审校开始：文档=%s，小节=%s，模型=%s，题数=%d，字符数=%d",
        document_name,
        section_name,
        model,
        exercise_count,
        len(content),
    )
    llm = _make_client(client, base_url, api_key, timeout_seconds)
    # 模板正文含大量 LaTeX 花括号，使用显式占位符替换，避免 ``str.format``
    # 把 ``\frac{}{}``、``\lim_{...}`` 等公式误当成格式字段。
    rendered_prompt = (
        BASIC_REVIEW_PROMPT_TEMPLATE.replace("{document_name}", document_name)
        .replace("{section_name}", section_name)
        .replace("{content}", content)
    )
    messages = [
        {
            "role": "system",
            "content": "严格执行用户给出的数学教材 Markdown 校对模板，不要添加模板之外的任务。",
        },
        {"role": "user", "content": rendered_prompt},
    ]
    response = _chat_text(llm, model, messages, reasoning_effort)
    report_usage(
        usage_callback,
        usage_from_response(response, operation="review.basic", model=model),
    )
    raw_response = _message_content(response)
    revised = (raw_response or "").strip()
    if revised.startswith("```") and revised.endswith("```"):
        revised = re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", revised).strip()
    # 模板使用 <markdown> 作为输入边界标记；模型偶尔会原样回显，
    # 这些标签不是实际内容，不能让它们进入最终 Markdown。
    revised = re.sub(r"^\s*<markdown>\s*", "", revised, flags=re.IGNORECASE)
    revised = re.sub(r"\s*</markdown>\s*$", "", revised, flags=re.IGNORECASE)
    if not revised:
        raise ValueError("LLM 返回中缺少有效 Markdown")

    _log_review_quality(content, revised, document_name, section_name)
    logger.info(
        "基础审校完成：文档=%s，小节=%s，内容%s变化",
        document_name,
        section_name,
        "有" if revised != content else "无",
    )
    return revised.rstrip() + "\n"


def review_basic(
    section: dict[str, Any],
    *,
    source_markdown: str | None = None,
    reference_markdown: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    timeout_seconds: int = DEFAULT_REVIEW_TIMEOUT_SECONDS,
    reasoning_effort: str = DEFAULT_REVIEW_REASONING_EFFORT,
    client: Any = None,
    usage_callback: UsageCallback | None = None,
) -> dict[str, Any]:
    """对一个公共 section JSON 做基础审校并返回新的 section JSON。

    调用方只需要传入一个 JSON-compatible ``dict``；Markdown 由实现根据
    ``section.content``/``section.exercises``生成，LLM 返回后再切回固定的
    section JSON。接口不修改输入对象。服务异常、空响应或无法切回有效题目
    时直接抛出异常；题数变化和低相似度只写入 ``review_warnings``，不回退。
    """
    from ..public_schema import normalize_public_section, public_section_from_node
    from ..parse_md_exercises import split_exercises, split_solutions_for_section

    normalized = normalize_public_section(section)
    document_name = normalized.get("document_name")
    if not document_name:
        raise ValueError("section.document_name 不能为空")
    section_name = normalized.get("path") or normalized.get("title")
    content_versions = normalized["content"]
    source = (
        content_versions.get("original")
        if reference_markdown is not None
        else source_markdown
        or content_versions.get("tool_review")
        or content_versions.get("basic_review")
        or content_versions.get("original")
    )
    if not source:
        raise ValueError("section.content.original 不能为空")

    if reference_markdown is not None:
        revised, warnings = _review_raw_source_two_pass(
            source,
            document_name,
            section_name,
            baseline_items=normalized["exercises"],
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
            reference_source=reference_markdown,
        )
        removed_answers = 0
    elif source_markdown:
        revised, warnings = _review_raw_source_two_pass(
            source,
            document_name,
            section_name,
            baseline_items=normalized["exercises"],
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
        )
        removed_answers = 0
    else:
        raw_revised = review_markdown_basic(
            source,
            document_name,
            section_name,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
        ).strip()
        revised, removed_answers = _remove_unexpected_answer_segments(
            source, raw_revised, allow_relocation=True
        )
        warnings = []
    reviewed_items = [
        item
        for item in split_exercises(
            revised, check_section_end=False, split_solutions=False
        )
        if item.get("number") != "__preamble__"
    ]
    if not reviewed_items:
        raise ValueError("LLM 返回的 Markdown 无法解析出编号习题")

    original_items = normalized["exercises"]
    # 公共 section 不额外暴露内部 doc_type；如果原始版本已有解答段，
    # 审校后的同一节沿用该策略，否则避免审校解析阶段再次把单个“证明：”
    # 误判为解答。
    split_solutions_for_section(
        reviewed_items,
        normalized.get("title") or normalized.get("path"),
        force=bool(source_markdown or reference_markdown)
        or any(item.get("solution_steps") for item in original_items),
    )
    for item in reviewed_items:
        for field in ("content", "solution_steps", "answer"):
            value = item.get(field)
            if isinstance(value, str):
                item[field] = _normalize_math_delimiters(value)
    if len(reviewed_items) != len(original_items):
        warnings.append(
            {
                "code": "exercise_count_changed",
                "original": len(original_items),
                "reviewed": len(reviewed_items),
                "severity": "high",
            }
        )
    if removed_answers:
        warnings.append(
            {"code": "added_answers_removed", "count": removed_answers}
        )
    similarity = _review_text_similarity(source, revised)
    if similarity < MIN_REVIEW_TEXT_SIMILARITY:
        warnings.append(
            {
                "code": "low_text_similarity",
                "similarity": round(similarity, 4),
                "threshold": MIN_REVIEW_TEXT_SIMILARITY,
                "severity": "high",
            }
        )

    # 优先按题号恢复页码、题型、原题号等 OCR 元数据；模型新增题目没有
    # 对应元数据时再按位置兜底，并使用固定字段默认值。
    unused_originals = set(range(len(original_items)))
    for index, item in enumerate(reviewed_items):
        item_numbers = {
            str(value)
            for value in (item.get("number"), item.get("original_number"))
            if value is not None
        }
        match_index = next(
            (
                original_index
                for original_index in sorted(unused_originals)
                if item_numbers
                & {
                    str(value)
                    for value in (
                        original_items[original_index].get("number"),
                        original_items[original_index].get("original_number"),
                    )
                    if value is not None
                }
            ),
            None,
        )
        if match_index is None and index in unused_originals:
            match_index = index
        if match_index is None:
            item["question_type"] = item.get("question_type") or "short_answer"
            continue
        unused_originals.remove(match_index)
        original_item = original_items[match_index]
        for field in (
            "pages",
            "question_type",
            "section",
            "section_key",
            "original_number",
            "bbox",
        ):
            if field in original_item:
                if field == "question_type" and item.get("question_type") == "proof":
                    continue
                item[field] = original_item[field]

    # Rebuild the editor-facing Markdown from the normalized exercise fields so
    # legacy labels (解答/证明/解法) cannot leak past review.
    revised = _render_structured_exercises(
        [
            {
                "number": str(item.get("number") or index + 1),
                "question": str(item.get("content") or ""),
                "solution_steps": str(item.get("solution_steps") or ""),
                "answer": str(item.get("answer") or ""),
                "_solution_kind": "proof"
                if item.get("question_type") == "proof"
                else "",
            }
            for index, item in enumerate(reviewed_items)
        ],
        original_items,
    )

    reviewed_node = {
        **normalized,
        "exercises": reviewed_items,
        "content": {
            "original": normalized["content"]["original"],
            "basic_review": revised.rstrip() + "\n",
            # 当前默认只启用基础审校，因此它同时就是最终版本；后续
            # 数学工具审校可再覆盖这个字段。
            "tool_review": revised.rstrip() + "\n",
        },
        "review_warnings": warnings,
    }
    result = public_section_from_node(
        reviewed_node,
        path=normalized["path"],
        document_name=document_name,
    )
    if result is None:  # pragma: no cover - reviewed_items 已做非空校验
        raise ValueError("审校结果没有有效习题")
    return result


def review_basic_exercise(
    section: dict[str, Any],
    exercise_indices: list[int],
    *,
    source_markdown: str | None = None,
    reference_markdown: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    timeout_seconds: int = DEFAULT_REVIEW_TIMEOUT_SECONDS,
    reasoning_effort: str = DEFAULT_REVIEW_REASONING_EFFORT,
    client: Any = None,
    usage_callback: UsageCallback | None = None,
) -> list[dict[str, Any]]:
    """审校 section 中指定下标的题目，返回对应 Exercise JSON 列表。

    多个下标会在一次 LLM 请求中提交，返回顺序与 ``exercise_indices`` 一致。
    """
    from ..parse_md_exercises import (
        PROOF_START_RE, SOLUTION_START_RE, split_exercises, split_solutions_for_section,
    )

    if not isinstance(section, dict) or not isinstance(exercise_indices, list):
        raise TypeError("section 必须是 JSON object，exercise_indices 必须是数组")
    from ..public_schema import normalize_public_section

    normalized = normalize_public_section(section)
    document_name = normalized.get("document_name")
    section_name = normalized.get("path") or normalized.get("title")
    if not document_name or not section_name:
        raise ValueError("section.document_name/path 不能为空")
    if not exercise_indices or any(
        isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= len(normalized["exercises"])
        for i in exercise_indices
    ):
        raise ValueError("exercise_indices 必须是非空且范围有效的整数数组")
    if len(set(exercise_indices)) != len(exercise_indices):
        raise ValueError("exercise_indices 不能包含重复下标")
    if source_markdown:
        reviewed = review_basic(
            section,
            source_markdown=source_markdown,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
        )
        reviewed_by_number = {
            str(item.get("number")): item for item in reviewed["exercises"]
        }
        numbers = [str(normalized["exercises"][i]["number"]) for i in exercise_indices]
        if any(number not in reviewed_by_number for number in numbers):
            raise ValueError("LLM 返回内容缺少选中的题目")
        return [reviewed_by_number[number] for number in numbers]
    if reference_markdown is not None:
        reviewed = review_basic(
            section,
            reference_markdown=reference_markdown,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
        )
        reviewed_by_number = {
            str(item.get("number")): item for item in reviewed["exercises"]
        }
        numbers = [str(normalized["exercises"][i]["number"]) for i in exercise_indices]
        if any(number not in reviewed_by_number for number in numbers):
            raise ValueError("LLM 返回内容缺少选中的题目")
        return [reviewed_by_number[number] for number in numbers]
    selected = [normalized["exercises"][i] for i in exercise_indices]
    if any(not (item.get("content") or "").strip() for item in selected):
        raise ValueError("选中的 exercise.content 不能为空")
    source_items = [dict(item) for item in selected]
    for source_item in source_items:
        if source_item.get("solution_steps"):
            continue
        question, embedded_solution = _split_embedded_solution(
            str(source_item.get("content") or "")
        )
        if embedded_solution:
            source_item["content"] = question
            source_item["solution_steps"] = embedded_solution
    source_node = {
        "has_exercise": True,
        "title": section_name,
        "exercises": source_items,
    }
    from ..postprocess import render_exercise_markdown

    source = render_exercise_markdown(source_node, include_section_headings=False)
    revised = review_markdown_basic(
        source,
        document_name,
        section_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_seconds=timeout_seconds,
        reasoning_effort=reasoning_effort,
        client=client,
        usage_callback=usage_callback,
    ).strip()
    revised, _ = _remove_unexpected_answer_segments(source, revised)
    items = [
        item
        for item in split_exercises(revised, check_section_end=False, split_solutions=False)
        if item.get("number") != "__preamble__"
    ]
    if len(items) != len(selected):
        raise ValueError("LLM 返回内容无法解析为指定数量的习题")
    split_solutions_for_section(
        items,
        section_name,
        force=(
            any(item.get("solution_steps") for item in selected)
            or any(
                SOLUTION_START_RE.match(line) or PROOF_START_RE.match(line)
                for item in items for line in str(item.get("content") or "").splitlines()
            )
        ),
    )
    for item in items:
        for field in ("content", "solution_steps", "answer"):
            value = item.get(field)
            if isinstance(value, str):
                item[field] = _normalize_math_delimiters(value)
    for item, original in zip(items, selected):
        original_solution = str(original.get("solution_steps") or "")
        original_content = str(original.get("content") or "")
        original_question, embedded_solution = _split_embedded_solution(original_content)
        if embedded_solution:
            reviewed_question, reviewed_embedded = _split_embedded_solution(
                str(item.get("content") or "")
            )
            if reviewed_embedded:
                item["content"] = reviewed_question
                item["solution_steps"] = "\n\n".join(
                    part for part in (reviewed_embedded, item.get("solution_steps") or "") if part
                )
            if _solution_was_truncated(
                embedded_solution, str(item.get("solution_steps") or "")
            ):
                logger.warning(
                    "单题审校疑似遗漏题干内解答，保留原始解答：文档=%s，题号=%s",
                    document_name,
                    original.get("number"),
                )
                item["solution_steps"] = embedded_solution
            if original_question and item.get("content") == original_content:
                item["content"] = original_question
        elif not original_solution and _solution_was_truncated(
            original_content,
            "\n".join(str(item.get(field) or "") for field in ("content", "solution_steps", "answer")),
        ):
            logger.warning(
                "单题审校疑似遗漏题干内容，保留原始题干：文档=%s，题号=%s",
                document_name,
                original.get("number"),
            )
            item["content"] = original_content
        if _solution_was_truncated(
            original_solution, str(item.get("solution_steps") or "")
        ):
            logger.warning(
                "单题审校疑似遗漏解答内容，保留原始解答：文档=%s，题号=%s",
                document_name,
                original.get("number"),
            )
            item["solution_steps"] = original_solution
        # 恢复原始题干或解答发生在首轮公式规范化之后，返回前再规范一次。
        for field in ("content", "solution_steps", "answer"):
            value = item.get(field)
            if isinstance(value, str):
                item[field] = _normalize_math_delimiters(value)
    result: list[dict[str, Any]] = []
    for item, original in zip(items, selected):
        for field in (
            "pages",
            "question_type",
            "section",
            "section_key",
            "original_number",
            "bbox",
        ):
            if field in original:
                if field == "question_type" and item.get("question_type") == "proof":
                    continue
                item[field] = original[field]
        result.append({
            "number": str(item.get("number") or original.get("number") or ""),
            "original_number": item.get("original_number"),
            "separator": item.get("separator") or original.get("separator") or ".",
            "content": item.get("content") or "",
            "solution_steps": item.get("solution_steps") or "",
            "answer": item.get("answer") or "",
            "question_type": item.get("question_type")
            or original.get("question_type")
            or "short_answer",
            "section": item.get("section"),
            "section_key": item.get("section_key"),
            "pages": list(item.get("pages") or []),
            "bbox": list(item.get("bbox") or []),
        })
    unresolved = [
        index for index, (original, reviewed) in enumerate(zip(selected, result))
        if _has_potential_unmarked_solution(str(original.get("content") or ""))
        and (
            not reviewed["solution_steps"]
            or _has_potential_unmarked_solution(str(reviewed["content"]))
            or _POTENTIAL_MATH_SOLUTION_RE.search(str(reviewed["solution_steps"]))
        )
    ]
    if unresolved:
        # 第一轮 Markdown 校对有时照抄了“解作…”；改用已有的结构化两轮
        # LLM 审校明确分配 question/solution_steps，不让通用切分器猜边界。
        focused_section = {
            **section,
            "exercises": source_items,
            "content": {"original": source},
        }
        focused = review_basic(
            focused_section,
            source_markdown=source,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            reasoning_effort=reasoning_effort,
            client=client,
            usage_callback=usage_callback,
        )
        focused_by_number = {
            str(item.get("number")): item for item in focused["exercises"]
        }
        for index in unresolved:
            number = str(selected[index]["number"])
            candidate = focused_by_number.get(number)
            if (
                not candidate
                or not str(candidate.get("solution_steps") or "").strip()
                or _has_potential_unmarked_solution(str(candidate.get("content") or ""))
                or _POTENTIAL_MATH_SOLUTION_RE.search(str(candidate.get("solution_steps") or ""))
            ):
                raise ValueError(f"题目 {number} 的已有解答未能从题干分离，请手动编辑或重试审校")
            result[index] = candidate
    return result
