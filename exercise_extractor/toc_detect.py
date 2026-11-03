"""目录树（TOC）层面的章节/习题检测逻辑。

负责从 toc_forge 的目录树判断：
- 哪些节点是习题章节（标题关键词匹配）
- 哪些是前后置内容（序/前言/附录等）应完全跳过
- 教材模式是否需要对小节末尾做节尾搜索（_should_use_tail_search）

与输入来源无关，纯目录树结构判断；后续适配习题集/试卷时可在此扩展
新的检测策略。
"""

import re
from typing import TypedDict


class TocNode(TypedDict, total=False):
    """toc_forge 目录树节点。"""

    title: str
    page_num: int | None
    children: list["TocNode"]

# 目录树里判定“习题章节”用的关键词。覆盖常见中英文教材的命名习惯
# （exercise / problem / question / practice / homework / 习题 / 练习 / 作业），
# 保持通用、不针对某本教材特调；若某教材命名特殊，改这个元组即可。
EXERCISE_TITLE_KEYWORDS = (
    "exercise",
    "problem",
    "question",
    "practice",
    "homework",
    "习题",
    "练习",
    "作业",
    # 思考题/例题（补充例题）：部分教材把它们作为独立目录项，
    # 内容为带解答的例题式题目，同样需要提取
    "思考题",
    "例题",
)

# 目录树里“习题答案”章节的关键词：整章/整节都是答案，需要单独处理
# （按小标题切分后回填到对应习题节点），而不是当普通习题章节提取。
ANSWER_SECTION_KEYWORDS = (
    "习题答案",
    "参考答案",
    "答案与提示",
    "习题解答",
)

# 目录中的前后置内容节点（序/前言/附录等）：完全跳过，不搜习题、不做节尾搜索
TOC_SKIP_TERMS = (
    "前言",
    "序言",
    "自序",
    "代序",
    "编者序",
    "绪言",
    "关于作者",
    "作者简介",
    "编者的话",
    "出版说明",
    "再版说明",
    "目录",
    "小结",
    "总结",
    "本章提要",
    "附表",
    "附录",
    "参考文献",
    "参考书",
    "索引",
    "后记",
    "致谢",
    "勘误",
    "术语表",
    "符号说明",
    "符号表",
    "介绍性实例",
    "introductory example",
    "preface",
    "foreword",
    "contents",
    "index",
    "references",
    "bibliography",
    "appendix",
    "acknowledg",
    "about the author",
    "glossary",
    "notation",
    "symbols",
)


def is_exercise_title(title: str) -> bool:
    # 目录 OCR 偶尔会把复合词拆开（如“习 题”），匹配前去掉空白噪声。
    t = re.sub(r"\s+", "", title.lower())
    if any(keyword in t for keyword in EXERCISE_TITLE_KEYWORDS if not keyword.isascii()):
        return True
    # A subject title mentioning "problems" is not an exercise collection
    # (e.g. "The Tangent and Velocity Problems", "Problem Solving").
    english = re.sub(r"^\s*\d+(?:[.-]\d+)*[.)]?\s*", "", title.lower()).strip()
    if re.match(r"problem[-\s]+solving\b", english):
        return False
    return bool(re.search(
        r"^(?:(?:(?:chapter|section|unit)\s+[\divxlc.-]+|review|additional|"
        r"supplementary|supplemental|challenge|practice|selected|further|mixed)\s+)*"
        r"(?:exercises?|problems?|questions?|practice|homework)\b",
        english,
    ))


def is_answers_title(title: str) -> bool:
    """是否为“习题答案”类目录项（整节都是答案，按小标题切分并回填匹配）。"""
    t = title.lower()
    return any(keyword in t for keyword in ANSWER_SECTION_KEYWORDS)


def is_skippable_title(title: str) -> bool:
    """前后置内容节点（序/前言/附录等）：完全跳过。"""
    if title.strip() == "序":
        return True
    t = title.lower()
    return any(term in t for term in TOC_SKIP_TERMS)


def _subtree_has_exercise(node: TocNode) -> bool:
    """节点的子树（子节点及更深层）里是否出现“习题”字样的节点。"""
    for child in node.get("children", []):
        if is_exercise_title(child["title"]) or _subtree_has_exercise(child):
            return True
    return False


def _child_has_exercise_toc(
    child: TocNode, next_sibling: TocNode | None
) -> bool:
    """子节点（一个小节）的习题是否有目录条目。

    满足任一即算有：
    - 自身就是习题节点（如“第 1 章补充习题”）
    - 下一个兄弟是习题节点（如“1.1 线性方程组”后面紧跟“习题1.1”）
    - 子树里含习题节点（如“第一节”下挂“习题1-1”）
    """
    if is_exercise_title(child.get("title", "")):
        return True
    if next_sibling and is_exercise_title(next_sibling.get("title", "")):
        return True
    return _subtree_has_exercise(child)


def should_use_tail_search(toc_tree: list[TocNode], doc_type: str) -> bool:
    """是否需要对每个小节末尾取几页找习题（教材模式）。

    规则：对所有有子节点的目录项，只看其中的“普通小节”子节点（习题节点自身
    不算，如章末的“第 1 章补充习题”），统计其子节点大多（>=50%）有目录习题条目
    的比例；若大多数（>=50%）目录项满足，说明小节后面的习题在目录里已被覆盖，
    就不再用“每个小节末尾取 3 页”的模式。

    子节点“有目录习题条目”指：下一个兄弟是习题节点（如“1.1 线性方程组”后面
    紧跟“习题1.1”），或子树里含习题节点（如“第一节”下挂“习题1-1”）。
    只含习题子节点的目录项（如“第一节”->“习题1-1”）不参与统计。
    """
    if doc_type != "textbook":
        return False
    parents_total = 0
    parents_covered = 0

    def collect(node: TocNode) -> None:
        nonlocal parents_total, parents_covered
        if is_skippable_title(node.get("title", "")):
            return
        children = node.get("children")
        if children:
            # 普通小节 = 非习题节点自身的子节点
            normal_children = [
                c for c in children if not is_exercise_title(c.get("title", ""))
            ]
            if normal_children:
                parents_total += 1
                covered = sum(
                    1
                    for idx, child in enumerate(children)
                    if not is_exercise_title(child.get("title", ""))
                    and _child_has_exercise_toc(
                        child, children[idx + 1] if idx + 1 < len(children) else None
                    )
                )
                if covered / len(normal_children) >= 0.5:
                    parents_covered += 1
            for child in children:
                collect(child)

    for node in toc_tree:
        collect(node)
    if not parents_total:
        return False
    return parents_covered / parents_total < 0.5
