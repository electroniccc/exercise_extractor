"""习题答案章节的处理：定位、按小标题切分、与习题节点匹配。

与输入来源无关，配合 main.py 使用：

1. `build_section_tree` 扫描目录时用 `is_answers_title` 识别“习题答案”类目录项，
   并给出页码范围：有 boundary 节点时以下一节点第一页收尾，否则到文档最后一页；
2. 本模块把答案页 markdown 按小标题切块（## 第N章 / # 第N篇 / 行首 §1.1），
   每块用 split_exercises 切成答案列表（重编号，原题号保留在 original_number）；
3. 按“篇/章”顺序 + 题号首段章号，把答案块匹配到已提取的习题节点，
   并校验两边题数是否一致（count_match：答案数 == 习题数时为 True）。

注意：有的教材答案只给部分题（如复变函数与积分变换的答案章节），
此时 count_match 为 False 属正常，匹配仍按标题/章号完成。
"""

import os
import re
from copy import deepcopy
from typing import TypedDict

from .ocr_cache import page_cache_is_ready
from .parse_md_exercises import (
    Exercise,
    HEADING_RE,
    _parse_number_label,
    normalize_section,
    page_marker,
    split_exercises,
)
from .html_tables import replace_html_tables


class AnswerSection(TypedDict, total=False):
    """习题答案章节里按小标题切出的一块。"""

    title: str  # 小标题（如 "第一篇 › 第1章" / "§1.1"）
    answers: list[Exercise]  # 该块内的答案（重编号 1,2,3…，原题号在 original_number）
    matched_title: str | None  # 匹配到的习题节点标题
    matched_path: str | None  # 匹配到的习题节点路径
    matched_count: int | None  # 匹配到的习题节点题数
    count_match: bool  # 答案数是否与习题数一致


# 行首 § 小节标签（梁昆淼《数学物理方法》答案区间的形态："§1.1 1.（1）..."）
_SECTION_LABEL_RE = re.compile(r"^\s*§\s*([\d.]+)\s*")
# 有些讲义的答案区没有 Markdown 标题，而是直接写“ 一、填空题 ”。要求题型
# 词，避免把“1. 0”“一、由定义…”等普通答案正文误当成分块标题。
_PLAIN_TYPE_SECTION_RE = re.compile(
    r"^\s*([一二三四五六七八九十]+)\s*[、.．]\s*"
    r"((?:单项?|多项?|不定项?)?选择题|填空题|解答题|简答题|计算题|证明题|"
    r"应用题|综合题|习题)\s*[：:]?\s*$"
)
# 答案常写为“1. 0”“4.正交”“2. $x=1$”。通用切分器为兼容 OCR 的
# “1. 1.7”多级题号，会把前者误解为 1.0；答案区有多个这种行时优先使用
# 本规则。答案编号后不强制空格，适配讲义常见的紧凑写法。
_SIMPLE_ANSWER_NUM_RE = re.compile(
    r"^\s*(\d{1,3})\s*[.．、]\s*(.+?)\s*$"
)

# 中文数字章号（覆盖常见 1~15 章）
_CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
    "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
}


def extract_chapter_number(text: str | None) -> int | None:
    """从标题/路径中提取章号："第1章"→1、"第一章"→1、"§2.3"→2、"习题 9-2"→9。"""
    t = re.sub(r"[\s*^{}$]+", "", text or "")
    m = re.search(r"第([一二三四五六七八九十]+)章", t)
    if m:
        return _CN_NUM.get(m.group(1))
    m = re.search(r"第(\d{1,2})章", t)
    if m:
        return int(m.group(1))
    m = re.search(r"§\s*(\d{1,2})", t)
    if m:
        return int(m.group(1))
    m = re.search(r"习题\s*(\d{1,2})", t)
    if m:
        return int(m.group(1))
    return None


def _is_section_title(text: str) -> bool:
    """答案章节自己的总标题（含“答案/提示”字样），不作为小标题。"""
    return "答案" in text or "提示" in text


def _renumber(items: list[Exercise]) -> None:
    """把答案按 1,2,3… 重编号，原题号保留在 original_number。"""
    seq = 0
    for item in items:
        if item.get("number") == "__preamble__":
            continue
        seq += 1
        if item["number"] != str(seq):
            item["original_number"] = item["number"]
            item["number"] = str(seq)
            item["separator"] = "."


def _split_simple_answer_lines(content: str) -> list[Exercise]:
    """切分“1. 答案”式的答案行，避免被误判为多级题号。"""
    answers: list[Exercise] = []
    current: Exercise | None = None
    for line in content.split("\n"):
        match = _SIMPLE_ANSWER_NUM_RE.match(line)
        if match:
            if current is not None:
                current["content"] = current["content"].strip()
                answers.append(current)
            current = {
                "number": match.group(1),
                "separator": ".",
                "content": match.group(2).strip(),
            }
        elif current is not None:
            current["content"] += "\n" + line
    if current is not None:
        current["content"] = current["content"].strip()
        answers.append(current)
    return answers


def split_answer_sections(content: str) -> list[AnswerSection]:
    """按小标题把答案内容切块。

    小标题的四种形态：
    - markdown 标题行（"# 第一篇" / "## 第1章"）：维护“篇 → 章”层级，
      块标题记作 "第一篇 › 第1章"；
    - 行首 § 标签（"§1.1 1.（1）…"）：作为小节标题，标签后的内容归属该块；
    - 裸的题型标题（"一、填空题" / "二、选择题"）：作为小节标题；
    - 总标题（含“答案/提示”字样的首个标题，如 "## 习题答案"）：跳过。
    """
    sections: list[AnswerSection] = []
    part: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_lines
        if current_title is not None and current_lines:
            body = "\n".join(current_lines).strip()
            simple_answer_lines = sum(
                bool(_SIMPLE_ANSWER_NUM_RE.match(line))
                for line in body.split("\n")
            )
            answers = (
                _split_simple_answer_lines(body)
                if simple_answer_lines >= 1
                else split_exercises(body, check_section_end=False)
            )
            answers = [
                e for e in answers if e.get("number") != "__preamble__"
            ]
            _renumber(answers)
            if answers:
                sections.append({"title": current_title, "answers": answers})
        current_title = None
        current_lines = []

    for line in content.split("\n"):
        if HEADING_RE.match(line):
            text = re.sub(r"^#+\s*", "", line).strip()
            if _is_section_title(text):
                continue  # 总标题（## 习题答案），不是小标题
            if _parse_number_label(text) is not None:
                # 题号（数字./数字-数字./数字.数字）被 OCR 误转成标题：
                # 不在这里切块，原样留给 split_exercises 的上下文规则判定还原
                current_lines.append(line)
                continue
            flush()
            if "篇" in text:
                # "第一篇"/"第二篇"：篇级标题，作为后续章级小标题的上下文
                # （不同教材 # 数量不统一，按文字判断更稳）
                part = text
                current_title = None
            else:
                current_title = f"{part} › {text}" if part else text
            continue
        m = _SECTION_LABEL_RE.match(line)
        if m:
            flush()
            current_title = f"§{m.group(1)}"
            rest = _SECTION_LABEL_RE.sub("", line).strip()
            if rest:
                current_lines.append(rest)
            continue
        plain_type_section = _PLAIN_TYPE_SECTION_RE.match(line)
        if plain_type_section:
            flush()
            current_title = (
                f"{plain_type_section.group(1)}、{plain_type_section.group(2)}"
            )
            continue
        if current_title is None:
            continue  # 总标题前的说明文字，忽略
        current_lines.append(line)
    flush()
    return sections


def _collect_exercise_targets(sections) -> list[dict]:
    """按目录顺序收集所有已提取习题的节点（含隐式节尾节点）。"""
    targets: list[dict] = []

    def walk(node, path: str) -> None:
        if node.get("has_exercise") and node.get("exercises"):
            exercises = [
                e for e in node["exercises"] if e.get("number") != "__preamble__"
            ]
            if not exercises:
                return
            title = node["title"]
            node_path = node.get("path") or path
            chapter = (
                extract_chapter_number(node_path)
                or extract_chapter_number(title)
            )
            if chapter is None:
                # 公共 JSON 中 original_number 可以合法为 null；回退到
                # 题号推断章号时必须传字符串给正则。
                m = re.match(
                    r"(\d{1,2})", exercises[0].get("original_number") or ""
                )
                if m:
                    chapter = int(m.group(1))
            targets.append(
                {
                    "title": title,
                    "path": node_path,
                    "chapter": chapter,
                    "count": len(exercises),
                    "implicit": bool(node.get("implicit")),
                    "node": node,
                }
            )
        for child in node.get("children", []):
            walk(child, f"{path} › {child['title']}" if path else child["title"])

    for s in sections:
        walk(s, s["title"])
    return targets


def _answer_section_kind(*titles: str | None) -> str | None:
    """从答案章节/答案块标题推断其对应的是思考题还是常规习题。"""
    title = normalize_section(" ".join(text or "" for text in titles))
    if "思考题" in title:
        return "thought"
    if any(keyword in title for keyword in ("习题", "练习", "作业")):
        return "exercise"
    return None


def _section_topic(title: str | None) -> str | None:
    """从题型式标题提取主题词，供 Markdown 裸题型标题的答案匹配兜底。"""
    text = normalize_section(title)
    for topic in ("填空", "选择", "计算", "解答", "简答", "证明", "应用", "综合"):
        if topic in text:
            return topic
    return None


def _target_kind(target: dict) -> str | None:
    """识别目标 section 是思考题还是常规习题。"""
    return _answer_section_kind(target.get("title")) or _answer_section_kind(
        target.get("path")
    )


def _eligible_targets(candidates: list[tuple[int, dict]], kind: str | None):
    """按答案块类型排除语义冲突的目标，未知类型目标仍作为兜底。"""
    if kind is None:
        return candidates
    same_kind = [item for item in candidates if _target_kind(item[1]) == kind]
    if same_kind:
        return same_kind
    return [item for item in candidates if _target_kind(item[1]) is None]


def _choose_answer_target(
    candidates: list[tuple[int, dict]],
    answer_section: AnswerSection,
    *,
    source_title: str | None,
    chapter_level: bool,
) -> tuple[int, dict] | None:
    """选择类型兼容且尽量题数相符的目标，最终保留目录顺序作为兜底。"""
    kind = _answer_section_kind(source_title, answer_section.get("title"))
    candidates = _eligible_targets(candidates, kind)
    if not candidates:
        return None

    if chapter_level:
        explicit = [item for item in candidates if not item[1]["implicit"]]
        if explicit:
            candidates = explicit

    # 泛化的“参考答案”等标题没有说明题型时，同章有多个候选且题数无法区分，
    # 默认优先常规习题，再用题数相符度打破同类候选之间的平局；明确标注
    # “思考题”的答案不受此默认影响，避免 OCR 分段题数刚好与思考题相同造成串配。
    if kind is None:
        exercises = [item for item in candidates if _target_kind(item[1]) == "exercise"]
        if exercises:
            candidates = exercises

    answer_count = _effective_answer_count(answer_section.get("answers", []))
    exact_count = [item for item in candidates if item[1]["count"] == answer_count]
    if exact_count:
        candidates = exact_count
    return candidates[0]


def match_answers(
    sections: list[dict], answer_section: dict
) -> list[dict]:
    """匹配一个答案章节 JSON，并返回填充答案后的 section JSON 列表。

    ``sections`` 必须是扁平公共 section 列表；``answer_section`` 是一个完整
    答案章节 object，包含 ``answer_sections`` 数组。函数不修改调用方传入的
    对象，匹配不到答案的 section 原样返回。
    """
    from .public_schema import normalize_public_section, public_section_from_node

    if not isinstance(sections, list):
        raise TypeError("sections 必须是 JSON array（Python list）")
    if not isinstance(answer_section, dict):
        raise TypeError("answer_section 必须是 JSON object（Python dict）")
    answer_blocks = answer_section.get("answer_sections")
    if not isinstance(answer_blocks, list):
        # 允许调用方直接传一个已经切好的答案块，便于单节调试；正式输出
        # 仍推荐使用完整答案章节的 answer_sections 数组。
        if isinstance(answer_section.get("answers"), list):
            answer_blocks = [answer_section]
        else:
            raise ValueError("answer_section.answer_sections 必须是 JSON array")

    copied_sections = [normalize_public_section(section) for section in deepcopy(sections)]
    copied_blocks = deepcopy(answer_blocks)
    targets = collect_exercise_targets(copied_sections)
    used: set[int] = set()

    for index, target in enumerate(targets):
        match_section_answers(
            copied_blocks,
            targets,
            index,
            used,
            source_title=answer_section.get("title"),
        )
        node = target["node"]
        # match_section_answers 将临时 answer_section 挂到 node 上；把公开的
        # 诊断信息固化后再清理临时字段。
        matched = node.pop("answer_section", None)
        if matched is None:
            node["answer_match"] = {
                "matched": False,
                "count_match": False,
                "source_path": None,
                "matched_count": None,
            }
        else:
            node["answer_match"] = {
                "matched": True,
                "count_match": bool(matched.get("count_match", False)),
                "source_path": matched.get("matched_path") or matched.get("title"),
                "matched_count": matched.get("matched_count"),
            }

    return [
        public_section_from_node(
            section,
            path=section.get("path"),
            document_name=section.get("document_name"),
        )
        for section in copied_sections
    ]


def match_answer_sections(
    answer_sections: list[AnswerSection],
    sections,
    *,
    source_title: str | None = None,
) -> None:
    """把答案块匹配到已提取的习题节点，并记录数量校验结果。

    策略：先按章号筛选，再依据答案源/目标标题区分思考题与常规习题；泛化
    答案标题在同章同时存在两类节点时默认匹配常规习题，并用答案题数消歧。
    章号相同但有多个同类候选（如复变的两篇都有第1章）时仍按顺序兜底。
    章级答案块（标题含“章”，如 "第一篇 › 第1章"）优先匹配显式节点，
    隐式节尾节点只作兜底。count_match 表示答案数与习题数是否一致。
    找不到章号匹配时退回标题包含匹配。
    """
    targets = _collect_exercise_targets(sections)
    used: set[int] = set()

    for sec in answer_sections:
        chapter = extract_chapter_number(sec["title"])
        indexed = list(enumerate(targets))
        candidates = [
            (i, target)
            for i, target in indexed
            if i not in used
            and chapter is not None
            and target["chapter"] == chapter
        ]
        if "章" in (sec["title"] or ""):
            # 章级答案块：优先显式习题节点，隐式节尾节点兜底
            best = _choose_answer_target(
                candidates,
                sec,
                source_title=source_title,
                chapter_level=True,
            )
        else:
            # §小节级答案块：显式/隐式节点都可
            best = _choose_answer_target(
                candidates,
                sec,
                source_title=source_title,
                chapter_level=False,
            )
        if best is None:
            sec_norm = normalize_section(sec["title"])
            title_candidates = [
                (i, target)
                for i, target in enumerate(targets)
                if i not in used
                and sec_norm
                and (
                    sec_norm == normalize_section(target["title"])
                    or sec_norm in normalize_section(target["path"])
                    or normalize_section(target["title"]) in sec_norm
                    or (
                        _section_topic(sec.get("title")) is not None
                        and _section_topic(sec.get("title"))
                        == _section_topic(target["title"])
                    )
                )
            ]
            best = _choose_answer_target(
                title_candidates,
                sec,
                source_title=source_title,
                chapter_level=False,
            )
        if best is not None:
            i, t = best
            used.add(i)
            sec["matched_title"] = t["title"]
            sec["matched_path"] = t["path"]
            sec["matched_count"] = t["count"]
            sec["count_match"] = (
                _effective_answer_count(sec["answers"]) == t["count"]
            )
            t["node"]["answer_match"] = {
                "matched": True,
                "count_match": bool(sec["count_match"]),
                "source_path": sec.get("matched_path") or sec.get("title"),
                "matched_count": sec.get("matched_count"),
            }
            # 临时挂到习题节点上，供后续 fill_exercise_answers 逐题填充用
            # （填充完成后会从节点上移除，不留在最终输出里）
            t["node"]["answer_section"] = sec
        else:
            sec["matched_title"] = None
            sec["matched_path"] = None
            sec["matched_count"] = None
            sec["count_match"] = False


def _number_key(num: str | None) -> str:
    """题号匹配键：统一区间连字符（4—6 与 4-6）、去空白。"""
    return str(num or "").replace("—", "-").replace("－", "-").strip()


def fill_exercise_answers(sections) -> None:
    """把答案块内容按题号逐题填到对应习题的 answer 字段。

    习题项与答案项按题号对齐（original_number 优先，其次 number），
    命中后写入 item["answer"]；习题原本已有内联答案（非空）的不覆盖。
    部分教材只给选做答案，未命中的习题 answer 保持空字符串。
    """

    def walk(node) -> None:
        if (
            node.get("has_exercise")
            and node.get("exercises")
            and node.get("answer_section")
        ):
            answers = _answer_key_map(node["answer_section"].get("answers", []))
            for item in node["exercises"]:
                if item.get("number") == "__preamble__":
                    continue
                if item.get("answer"):
                    continue
                key = _number_key(item.get("original_number") or item.get("number"))
                matched = answers.get(key)
                if matched is not None:
                    item["answer"] = matched.get("content", "")
        for child in node.get("children", []):
            walk(child)

    for s in sections:
        walk(s)


def _answer_key_map(answer_items: list[Exercise]) -> dict[str, Exercise]:
    """把答案项按题号建索引；区间答案（"6-7"）展开到覆盖的每个题号。"""
    mapping: dict[str, Exercise] = {}
    for a in answer_items:
        mapping[_number_key(a.get("original_number") or a.get("number"))] = a
    for a in answer_items:
        key = _number_key(a.get("original_number") or a.get("number"))
        m = re.match(r"^(\d{1,3})-(\d{1,3})$", key)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if 0 < end - start <= 20:
                for n in range(start, end + 1):
                    mapping.setdefault(str(n), a)
    return mapping


def _effective_answer_count(answer_items: list[Exercise]) -> int:
    """答案项的有效题数：区间答案（"6-7"）按覆盖的题号逐个计数。"""
    total = 0
    for a in answer_items:
        key = _number_key(a.get("original_number") or a.get("number"))
        m = re.match(r"^(\d{1,3})-(\d{1,3})$", key)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if 0 < end - start <= 20:
                total += end - start + 1
                continue
        total += 1
    return total


def collect_exercise_targets(sections) -> list[dict]:
    """按目录顺序收集所有已提取习题的节点（含隐式节尾节点）。

    流式场景用：预计算全树 targets（带 path），逐节匹配答案块。
    """
    return _collect_exercise_targets(sections)


def match_section_answers(
    answer_sections: list[AnswerSection],
    targets: list[dict],
    target_index: int,
    used: set[int],
    *,
    source_title: str | None = None,
) -> None:
    """把最早的可用答案块匹配给第 target_index 个习题节点并逐题填充。

    流式提取时按树序逐节调用；答案源标题用于避免思考题与常规习题串配。
    章级答案块优先显式节点，隐式节尾节点只作兜底；标题归一化再兜底。
    """
    target = targets[target_index]
    chosen: tuple[int, AnswerSection] | None = None
    for i, sec in enumerate(answer_sections):
        if i in used:
            continue
        chapter = extract_chapter_number(sec["title"])
        kind = _answer_section_kind(source_title, sec.get("title"))
        if not _eligible_targets([(target_index, target)], kind):
            continue
        if (
            kind is None
            and _target_kind(target) == "thought"
            and chapter is not None
            and target["chapter"] == chapter
            and any(
                other["chapter"] == chapter
                and _target_kind(other) == "exercise"
                for other in targets
            )
        ):
            # 泛化答案章节没有题型提示时，遍历顺序不能让思考题抢先消费章级答案。
            continue
        # 章级答案块：当前是隐式节尾节点且同章还有显式节点时跳过
        if (
            "章" in (sec["title"] or "")
            and target["implicit"]
            and any(
                t["chapter"] == target["chapter"] and not t["implicit"]
                for t in targets
            )
        ):
            continue
        if chapter is not None and target["chapter"] == chapter:
            chosen = (i, sec)
            break
        # 标题归一化兜底
        sec_norm = normalize_section(sec["title"])
        t_title = normalize_section(target["title"])
        t_path = normalize_section(target["path"])
        if sec_norm and (
            sec_norm == t_title
            or sec_norm in t_path
            or t_title in sec_norm
            or (
                _section_topic(sec.get("title")) is not None
                and _section_topic(sec.get("title")) == _section_topic(target["title"])
            )
        ):
            chosen = (i, sec)
            break
    if chosen is None:
        return
    i, sec = chosen
    used.add(i)
    sec["matched_title"] = target["title"]
    sec["matched_path"] = target["path"]
    sec["matched_count"] = target["count"]
    sec["count_match"] = _effective_answer_count(sec["answers"]) == target["count"]
    target["node"]["answer_match"] = {
        "matched": True,
        "count_match": bool(sec["count_match"]),
        "source_path": sec.get("matched_path") or sec.get("title"),
        "matched_count": sec.get("matched_count"),
    }
    # 临时挂载到习题节点，供 fill_exercise_answers 逐题填充
    target["node"]["answer_section"] = sec
    fill_exercise_answers([target["node"]])


def _combine_pages(page_range: list[int], markdown_path: str) -> str:
    """拼接答案章节各页 markdown（页间插入页码标记）。"""
    content = ""
    for page_num in range(page_range[0], page_range[1]):
        path = os.path.join(markdown_path, f"page_{page_num}", "page.md")
        if page_cache_is_ready(markdown_path, page_num):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content += f"\n{page_marker(page_num)}\n{f.read()}\n"
    return content


def extract_and_match(
    sections,
    markdown_path: str,
    *,
    match_enabled: bool = True,
) -> None:
    """提取答案 section，并按需匹配回习题。

    ``match_enabled=False`` 仍会完成答案页拼接和切块，使调用方能够展示
    答案 tab；但不会修改任何习题的 answer/solution_steps 字段。
    """
    answers_nodes: list = []

    def collect(nodes) -> None:
        for n in nodes:
            if n.get("is_answers"):
                answers_nodes.append(n)
            collect(n.get("children", []))

    collect(sections)
    for node in answers_nodes:
        content = _combine_pages(node["exercise_page_range"], markdown_path)
        answer_sections = split_answer_sections(content)
        # HTML 表格转 markdown 表格（单元格内 LaTeX 渲染问题）
        for sec in answer_sections:
            for item in sec.get("answers", []):
                for field in ("content", "solution_steps", "answer"):
                    text = item.get(field)
                    if text:
                        new_text = replace_html_tables(text)
                        if new_text != text:
                            item[field] = new_text
        if match_enabled:
            match_answer_sections(
                answer_sections,
                sections,
                source_title=node.get("title"),
            )
        node["answer_sections"] = answer_sections
    # 逐题填充：把答案内容填进对应习题的 answer 字段
    if match_enabled:
        fill_exercise_answers(sections)
    # 填充用的临时挂载不留在最终输出里（答案节点上的 answer_sections 保留）

    def cleanup(nodes) -> None:
        for n in nodes:
            n.pop("answer_section", None)
            cleanup(n.get("children", []))

    cleanup(sections)
