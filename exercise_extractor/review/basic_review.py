"""基础审校：按模板修复 OCR 文字、标点与 KaTeX/LaTeX 格式，只返回 Markdown。"""

from __future__ import annotations

import json
import logging
import os
import re
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# 与 ocr.py 中 toc_forge.bookmark_pdf 使用同一套阿里云 OpenAI-compatible 地址；
# 调用接口时仍可通过 base_url 覆盖为其他兼容服务。
DEFAULT_REVIEW_MODEL = "deepseek-v4-flash-0731"
DEFAULT_REVIEW_BASE_URL = (
    "https://ws-alorrjihwnhn7hw7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
)

# exercise_extractor 的 Markdown 输出格式：*1.*、*4-6.*、*1.2.3.* 题目……
_EXERCISE_START_RE = re.compile(
    r"(?m)^\s*\*?((?:\d+(?:\.\d+)+|\d+(?:\s*[—–－-]\s*\d+)?))"
    r"\s*[.．、)）]\s*\*?\s+"
)
_HEADING_RE = re.compile(r"(?m)^\s*#{1,6}\s+\S.*$")
_ANSWER_MARKER_RE = re.compile(r"(?m)^\s*(?:\*\*\s*)?(解|答)(?:\s*\*\*)?\s*[：:]\s*")
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

请对这部分内容做校对，最终只输出校对后的Markdown正文本身，不要添加任何解释、总结或前后缀说明。

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



def _exercise_segments(markdown: str) -> list[str]:
    """按题号切出题目片段，供审校前后的保守比较使用。"""
    starts = list(_EXERCISE_START_RE.finditer(markdown))
    return [
        markdown[start.start() : starts[index + 1].start() if index + 1 < len(starts) else len(markdown)]
        for index, start in enumerate(starts)
    ]


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


def _remove_unexpected_answer_segments(
    original: str, revised: str
) -> tuple[str, int]:
    """删除模型给原本没有解/答的题目补出的解答段。

    只有题号序列仍能对齐时才做清理；题号结构已经变化时交给后续校验回退，
    避免把不确定的文本误删。返回 (清理后的 Markdown, 删除的题目数)。
    """
    original_numbers = _exercise_signatures(original)
    revised_numbers = _exercise_signatures(revised)
    if original_numbers != revised_numbers:
        return revised, 0
    original_kinds = _answer_marker_kinds_by_exercise(original)
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


def _make_client(client: Any, base_url: str | None, api_key: str | None) -> Any:
    if client is not None:
        return client
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise RuntimeError("审校接口需要安装 openai：pip install openai") from exc
    resolved_key = (
        api_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ALIYUN_API_KEY")
    )
    if not resolved_key:
        raise ValueError("需要 api_key，或设置环境变量 OPENAI_API_KEY")
    kwargs: dict[str, Any] = {"api_key": resolved_key}
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
) -> Any:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    # 仅供暂存的 math review 使用；basic review 走下方的纯文本调用。
    if not tools:
        kwargs["response_format"] = {"type": "json_object"}
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
) -> Any:
    """调用不带结构化输出约束的文本接口，供 basic review 使用。"""
    return client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        # 基础审校只做文字、标点和 KaTeX 格式修复，显式关闭支持该参数的
        # 模型的思考模式，避免无必要的推理延迟和额外 token。
        extra_body={"enable_thinking": False},
    )


def review_markdown_basic(
    content: str,
    document_name: str,
    section_name: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    client: Any = None,
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
    llm = _make_client(client, base_url, api_key)
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
    raw_response = _message_content(_chat_text(llm, model, messages))
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
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = DEFAULT_REVIEW_MODEL,
    client: Any = None,
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
        content_versions.get("tool_review")
        or content_versions.get("basic_review")
        or content_versions.get("original")
    )
    if not source:
        raise ValueError("section.content.original 不能为空")

    raw_revised = review_markdown_basic(
        source,
        document_name,
        section_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        client=client,
    ).strip()
    revised, removed_answers = _remove_unexpected_answer_segments(
        source, raw_revised
    )
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
        force=any(item.get("solution_steps") for item in original_items),
    )
    warnings: list[dict[str, Any]] = []
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

    # 按题目顺序恢复页码、题型、原题号等 OCR 元数据；模型新增题目没有
    # 对应元数据时仍保留模型结果，但使用固定字段默认值。
    for index, item in enumerate(reviewed_items):
        if index >= len(original_items):
            item["question_type"] = item.get("question_type") or "short_answer"
            continue
        original_item = original_items[index]
        for field in (
            "pages",
            "question_type",
            "section",
            "section_key",
            "original_number",
        ):
            if field in original_item:
                item[field] = original_item[field]

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
