"""场景化测试：每个 test/test_scenarios/*.txt 对应一个测试函数。

测试启动后依次扫描 test/test_scenarios 目录，按文件名经 SCENARIO_TESTS
分发到这里的函数；多页场景（textbook_fake_cache_page1/2.txt）由主文件
注册，附属文件由主场景测试函数自行加载。
"""

import json
import os
import tempfile

from .common import _exampaper_node, check, load_scenario


def test_textbook_fake_cache() -> None:
    """伪造缓存的离线提取管线：combine → extract → write_outputs。"""
    from exercise_extractor.extraction import combine_section_pages, extract_exercises
    from exercise_extractor.postprocess import write_outputs

    with tempfile.TemporaryDirectory() as tmp:
        md_path = os.path.join(tmp, "markdown")
        os.makedirs(os.path.join(md_path, "page_1"))
        with open(
            os.path.join(md_path, "page_1", "page.md"), "w", encoding="utf-8"
        ) as f:
            f.write(load_scenario("textbook_fake_cache_page1.txt"))
        os.makedirs(os.path.join(md_path, "page_2"))
        with open(
            os.path.join(md_path, "page_2", "page.md"), "w", encoding="utf-8"
        ) as f:
            f.write(load_scenario("textbook_fake_cache_page2.txt"))
        for page_num in (1, 2):
            page_dir = os.path.join(md_path, f"page_{page_num}")
            with open(os.path.join(page_dir, "page.json"), "w", encoding="utf-8") as f:
                json.dump({"parsing_res_list": []}, f)
            with open(os.path.join(page_dir, "READY"), "w", encoding="utf-8") as f:
                f.write("ready\n")
        node = {
            "title": "习题 1-1",
            "has_exercise": True,
            "children": [],
            "exercise_page_range": [1, 3],
            "boundary_title": "第二节",
        }
        combine_section_pages([node], md_path)
        extract_exercises([node])
        exs = node["exercises"]
        check("伪造缓存提取 2 题", len(exs) == 2, f"got {len(exs)}")
        check(
            "课本习题默认简答题",
            all(e["question_type"] == "short_answer" for e in exs),
        )
        check(
            "解切到 solution_steps",
            exs[0]["solution_steps"].startswith("解答一"),
        )
        check("答切到 answer", exs[1]["answer"] == "答案二")
        check("边界页习题被截断", all(e["number"] != "3" for e in exs))
        outdir = os.path.join(tmp, "out")
        report = write_outputs([node], outdir, md_path)
        check("write_outputs 报告总数", report["total_exercises"] == 2)
        md_file = os.path.join(outdir, "1_习题_1-1.md")
        check("每节 md 文件生成", os.path.exists(md_file))
        with open(md_file, encoding="utf-8") as f:
            text = f.read()
        check(
            "md 输出含解/答",
            "**解**: 解答一" in text and "**答**: 答案二" in text,
        )
        with open(os.path.join(outdir, "1_习题_1-1.json"), encoding="utf-8") as f:
            data = json.load(f)
        check(
            "每节 JSON 含公共 section",
            data["path"] == "习题 1-1"
            and "children" not in data
            and data["exercises"][0]["content"].startswith("第一题"),
        )


def test_exercise_book_prefix() -> None:
    """习题集“习题 N.”前缀归一。"""
    from exercise_extractor import exercise_book

    content = load_scenario("exercise_book_prefix.txt")
    check(
        "习题前缀归一",
        exercise_book._normalize_exercise_prefixes(content) == "1. a\n2. b\n",
    )


def test_textbook_decimal_display_formula() -> None:
    """显示公式中的 74.000 不能被误判为多级题号。"""
    from exercise_extractor.extraction import extract_exercises
    from exercise_extractor.public_schema import public_section_from_node

    node = {
        "title": "习题",
        "has_exercise": True,
        "children": [],
        "exercise_content": load_scenario("textbook_decimal_display_formula.txt"),
    }
    chapter = {"title": "第七章 参数估计", "children": [node]}
    extract_exercises([chapter])
    exercises = node["exercises"]
    check(
        "公式小数不截断后续习题",
        [item["number"] for item in exercises] == ["1", "2", "3", "4", "5", "6"],
        f"got {[item['number'] for item in exercises]}",
    )
    check(
        "公式内小数保留在第一题",
        "74.000\\quad73.998\\quad74.006\\quad74.002"
        in exercises[0]["content"],
    )
    check(
        "第二题从真实题干开始",
        exercises[1]["content"].startswith("设  $ X_{1}")
        and "74.000" not in exercises[1]["content"],
    )
    public = public_section_from_node(
        node,
        path="第七章 参数估计/习题",
        document_name="测试教材",
    )
    check(
        "泛化习题公共 section title 使用章节路径",
        public is not None
        and public["title"] == "第七章 参数估计 习题"
        and public["path"] == "第七章 参数估计/习题",
    )


def test_textbook_chapter_prefixed_numbers() -> None:
    """目录 OCR 空格噪声及“章-题”编号不能漏节或粘题。"""
    from exercise_extractor.extraction import build_section_tree, extract_exercises
    from exercise_extractor.parse_md_exercises import (
        numbers_are_consecutive,
        split_exercises,
    )
    from exercise_extractor.toc_detect import is_skippable_title

    toc = [
        {
            "title": "第一章",
            "page_num": 1,
            "children": [
                {"title": "本章提要", "page_num": 2, "children": []},
                {"title": "思考题", "page_num": 3, "children": []},
                {"title": "习 题", "page_num": 4, "children": []},
                {"title": "习题答案", "page_num": 6, "children": []},
            ],
        }
    ]
    sections = build_section_tree(toc, doc_type="textbook", doc_page_count=10)
    chapter = sections[0]
    titles = [node["title"] for node in chapter["children"]]
    exercise = next(node for node in chapter["children"] if node["title"] == "习 题")
    check(
        "目录空格噪声仍识别习题节点",
        exercise.get("has_exercise")
        and not exercise.get("implicit")
        and "本章提要" not in titles,
        str([(n["title"], n.get("has_exercise"), n.get("implicit")) for n in chapter["children"]]),
    )
    check(
        "本章提要标题跳过",
        is_skippable_title("本章提要"),
    )

    content = load_scenario("textbook_chapter_prefixed_numbers.txt")
    exercise["exercise_content"] = content
    extract_exercises([chapter])
    extracted = exercise.get("exercises", [])
    check(
        "目录节点按页内标题提取出章末习题",
        len(extracted) == 2
        and [item.get("original_number") for item in extracted] == ["1-1", "1-2"],
        str([(item.get("number"), item.get("original_number")) for item in extracted]),
    )
    exercises = split_exercises(content, check_section_end=False)
    check(
        "章-题编号及 OCR 波浪线均逐题切分",
        [item["number"] for item in exercises]
        == ["1-1", "1-2", "1-11", "1-12", "1-13", "1-14", "1-1", "1-2"],
        str([item["number"] for item in exercises]),
    )
    intervals = split_exercises(
        "3. 前一道题。\n4-6. 区间题。\n7. 后一道题。",
        check_section_end=False,
    )
    check(
        "真正的区间题号仍独立切分",
        [item["number"] for item in intervals] == ["3", "4-6", "7"],
        str([item["number"] for item in intervals]),
    )
    check(
        "章-题编号可通过连续性校验",
        numbers_are_consecutive(["1-1", "1-2", "1-4"]),
    )
    check(
        "区间题号与后续普通题号可连续",
        numbers_are_consecutive(["4-6", "7"]),
    )


def test_exercise_book_no_toc() -> None:
    """习题集无目录：整本按小标题层级切块。"""
    from exercise_extractor import exercise_book

    items = exercise_book._extract_no_toc(
        load_scenario("exercise_book_no_toc.txt")
    )
    check(
        "无目录按小标题切块",
        len(items) == 3
        and all(
            i["section"] in ("1.1 数列", "1.2 连续函数") for i in items
        ),
    )
    check(
        "习题集习题默认简答题",
        all(i["question_type"] == "short_answer" for i in items),
    )
    node = {
        "title": "习题 1",
        "has_exercise": True,
        "children": [],
        "exercise_page_range": [4, 6],
        "exercise_content": (
            "<!-- page: 4 -->\n## 习题 1\n1. 第一题\n"
            "<!-- page: 5 -->\n2. 第二题"
        ),
    }
    exercise_book.extract_exercises([node])
    check(
        "习题集有目录习题带页码",
        [e.get("pages") for e in node["exercises"]] == [[4, 4], [5, 5]],
    )


def test_answers_split() -> None:
    """答案章节按小标题切块。"""
    from exercise_extractor.answers import split_answer_sections

    ans_secs = split_answer_sections(load_scenario("answers_split_sections.txt"))
    check(
        "答案按小标题切块",
        len(ans_secs) == 2
        and ans_secs[0]["title"] == "第1章"
        and len(ans_secs[0]["answers"]) == 2
        and ans_secs[1]["answers"][0]["content"].startswith("三"),
    )


def test_exampaper_recent() -> None:
    """近十年真题：连续编号 + 三种题型 + 无答案区。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_recent_paper.txt")
    secs, tail = exampaper.split_type_sections(content)
    node = _exampaper_node("2023", content)
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("近真题题型小节数", len(secs) == 3, f"got {len(secs)}")
    check("近真题提取题数", len(exs) == 6, f"got {len(exs)}")
    check(
        "近真题题型标注",
        [e["question_type"] for e in exs]
        == [
            "single_choice",
            "single_choice",
            "fill_in_blank",
            "fill_in_blank",
            "short_answer",
            "short_answer",
        ],
        str([e["question_type"] for e in exs]),
    )
    check(
        "近真题题号保留",
        [e["number"] for e in exs] == ["1", "2", "11", "12", "17", "18"],
    )
    check(
        "近真题习题带页码",
        all(e.get("pages") == [1, 1] for e in exs),
    )
    check("近真题无答案区", tail is None)
    check("解答题子问不拆", "(1) 求" in exs[4]["content"])
    check(
        "近真题答案为空",
        all(
            e.get("answer") == "" and e.get("solution_steps") == ""
            for e in exs
        ),
    )


def test_exampaper_old() -> None:
    """早年真题：无题型词小节 + 节内（1）（2）编号。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_old_paper.txt")
    secs, _ = exampaper.split_type_sections(content)
    node = _exampaper_node("1987", content)
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("旧真题小节数", len(secs) == 5, f"got {len(secs)}")
    check("旧真题总题数", len(exs) == 11, f"got {len(exs)}")
    types_old = [e["question_type"] for e in exs]
    check(
        "旧真题题型",
        types_old.count("fill_in_blank") == 7
        and types_old.count("single_choice") == 2
        and types_old.count("short_answer") == 2,
        str(types_old),
    )
    check(
        "旧真题节内(1)转题号",
        exs[0]["number"] == "1" and exs[4]["number"] == "5",
    )
    check(
        "无题型词小节兜底简答题",
        exs[5]["question_type"] == "short_answer"
        and exs[5]["number"] == "1"
        and exs[5]["section_key"] == "二",
    )
    check(
        "简答题子问不拆",
        "（1）（本题满分3分）" in exs[6]["content"],
    )


def test_exampaper_chinese_scored_questions() -> None:
    """中文题号后带分值说明时，仍按大题边界切分。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_chinese_scored_questions.txt")
    sections, _ = exampaper.split_type_sections(content)
    node = _exampaper_node("数学专业类旧卷", content)
    exampaper.extract_exercises([node])
    exercises = node["exercises"]
    check(
        "带分值的中文大题识别为独立小节",
        len(sections) == 3
        and [section["section_key"] for section in sections]
        == ["一", "二", "三"],
        str([
            (section["title"], section["section_key"])
            for section in sections
        ]),
    )
    check(
        "中文大题不再整卷漏提或合并",
        len(exercises) == 3
        and [item["content"].splitlines()[0] for item in exercises]
        == [
            "（本题 15 分）在空间直角坐标系中设马鞍面 S。求点 P。",
            "（本题 15 分）A=(aᵢⱼ) 为 n 阶实方阵。求规范形。",
            "（本题 10 分）证明结论。",
        ],
        str([
            (item.get("section_key"), item.get("content", ""))
            for item in exercises
        ]),
    )


def test_exampaper_declared_multiple_subquestions() -> None:
    """题型标题声明多小题时，解答/计算题的括号编号应切开。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_declared_multiple_subquestions.txt")
    node = _exampaper_node("早期非数学类试卷", content)
    exampaper.extract_exercises([node])
    exercises = node["exercises"]
    check(
        "本题共五小题拆为五题并保留后续大题",
        len(exercises) == 7
        and [item["number"] for item in exercises]
        == ["1", "2", "3", "4", "5", "1", "1"]
        and [item["section_key"] for item in exercises]
        == ["一、计算题"] * 5 + ["二", "三"],
        str([
            (item.get("number"), item.get("section_key"))
            for item in exercises
        ]),
    )
    check(
        "括号小题边界和大题内容不串题",
        exercises[0]["content"].startswith("求 1 + 1。")
        and exercises[4]["content"].startswith("求 5 + 5。")
        and exercises[5]["content"].startswith("（本题 15 分）设函数 g(x)=x。")
        and exercises[6]["content"].startswith("（本题 10 分）求积分 I。"),
    )


def test_exampaper_scoring_tables_and_instructions() -> None:
    """试卷说明与评分栏不混入题目，实际数据表仍保留。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_scoring_tables_and_instructions.txt")
    node = _exampaper_node("非数学类试卷", content)
    exampaper.extract_exercises([node])
    exercises = node["exercises"]
    check(
        "带每小题分值的一、标题识别为第一大题",
        len(exercises) == 6
        and [item["section_key"] for item in exercises] == ["一"] * 5 + ["二"]
        and [item["number"] for item in exercises] == ["1", "2", "3", "4", "5", "1"],
        str([(item["section_key"], item["number"]) for item in exercises]),
    )
    check(
        "注意事项和评分表不进入题目",
        all(
            not any(
                text in item["content"]
                for text in ("所有答题", "密封线", "得分", "评阅人", "总分")
            )
            for item in exercises
        ),
    )
    check(
        "小题内容与真正的数据表保留",
        len(exercises) == 6
        and exercises[0]["content"] == "求第一个极限。"
        and exercises[2]["content"] == "求第三个极限。"
        and "| x | 1 | 2 |" in exercises[-1]["content"],
    )


def test_exampaper_inline_jieda() -> None:
    """试题+解析：内嵌【答案】/【解】拆到 answer / solution_steps。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "1987解析", load_scenario("exampaper_inline_jieda.txt")
    )
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("解析文档题数", len(exs) == 5, f"got {len(exs)}")
    check(
        "【答案】→ answer",
        exs[0]["answer"] == "x−y+z=0" and exs[3]["answer"] == "（C）",
    )
    check(
        "【解】→ solution_steps",
        exs[0]["solution_steps"].startswith("直线方向向量")
        and exs[2]["solution_steps"].startswith("由极限"),
    )


def test_exampaper_tail_answers() -> None:
    """试题+文末答案：按小节键+题号回填。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_tail_answers.txt")
    _, tail = exampaper.split_type_sections(content)
    check("文末答案区识别", tail is not None and "参考答案" in tail)
    node = _exampaper_node("2024真题+答案", content)
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("真题+答案题数", len(exs) == 6, f"got {len(exs)}")
    check(
        "选择题答案回填",
        exs[0]["answer"] == "C" and exs[1]["answer"] == "A",
    )
    check(
        "填空题答案回填",
        exs[2]["answer"] == "3" and exs[3]["answer"] == "-1",
    )
    check(
        "解答题解析回填",
        exs[4]["solution_steps"].startswith("设 y=y(x)")
        and exs[5]["solution_steps"].startswith("令 f'=0"),
    )


def test_exampaper_parenthesized_answers() -> None:
    """题目因“共N小题”拆分后，答案区相同的括号编号逐题回填。"""
    from exercise_extractor import exampaper

    content = load_scenario("exampaper_parenthesized_answers.txt")
    node = _exampaper_node("括号编号题目及答案", content)
    exampaper.extract_exercises([node])
    exercises = node["exercises"]
    check(
        "括号答案逐题回填",
        [exercise["number"] for exercise in exercises] == ["1", "2", "3", "4", "5"]
        and all(
            answer in exercise["solution_steps"]
            for exercise, answer in zip(
                exercises, (f"答案{number}。" for number in ("一", "二", "三", "四", "五"))
            )
        ),
        str([(exercise["number"], exercise["solution_steps"]) for exercise in exercises]),
    )
    check(
        "AI 审校原文保留题型对应的文末答案",
        node["children"][0]["_review_source_markdown"].count("答案") >= 5
        and "**解**" in node["children"][0]["_review_source_markdown"],
    )


def test_exampaper_paren_headings() -> None:
    """解析文档：括号题号被 OCR 渲染成标题时降级还原。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "2023解析", load_scenario("exampaper_paren_headings.txt")
    )
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check(
        "括号题号标题降级",
        [e["number"] for e in exs] == ["17", "18"]
        and all(e["question_type"] == "short_answer" for e in exs),
        str([(e["number"], e["question_type"]) for e in exs]),
    )
    check(
        "解答题解析回填(括号)",
        exs[0]["solution_steps"].startswith("(I) 求导得")
        and exs[1]["solution_steps"].startswith("令 f'=0"),
    )


def test_exampaper_multi_choice_proof() -> None:
    """多项选择题 + 证明题的标准英文类型。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "多选+证明", load_scenario("exampaper_multi_choice_proof.txt")
    )
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check(
        "多选题/证明题英文类型",
        [e["question_type"] for e in exs]
        == ["multiple_choice", "multiple_choice", "proof"],
        str([e["question_type"] for e in exs]),
    )


def test_exampaper_single_multi_choice() -> None:
    """“单选题/多选题”小节标题识别（不带“题”字后缀的变体）。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "单选+多选", load_scenario("exampaper_single_multi_choice.txt")
    )
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check(
        "单选题/多选题小节识别",
        [e["question_type"] for e in exs]
        == ["single_choice", "multiple_choice"]
        and [e["section_key"] for e in exs]
        == ["一、单选题", "二、多选题"],
        str([(e["question_type"], e.get("section_key")) for e in exs]),
    )


def test_exampaper_table_tail() -> None:
    """无标题答案区：题号/答案表格 + 【详解】同行拆分 + 全卷按题号回填。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "春季卷", load_scenario("exampaper_table_tail.txt")
    )


    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("表格答案区识别", len(exs) == 4, f"got {len(exs)}")
    check(
        "无标题答案区按题号回填",
        exs[0]["answer"] == "(0,+∞)"
        and exs[2]["answer"] == "B"
        and exs[3]["answer"] == "(1) ω=1",
        str([(e["number"], e.get("answer")) for e in exs]),
    )
    check(
        "【详解】同行拆分",
        exs[1]["solution_steps"].startswith("设倾斜角为 θ")
        and exs[3]["solution_steps"].startswith("由周期公式得"),
        str([(e["number"], e.get("solution_steps", "")[:20]) for e in exs]),
    )


def test_exampaper_duplicate_number_solution() -> None:
    """OCR 题号转义且解析再次带题号时，不拆成两道题。"""
    from exercise_extractor import exampaper

    node = _exampaper_node(
        "重复题号解析", load_scenario("exampaper_duplicate_number_solution.txt")
    )
    exampaper.extract_exercises([node])
    exs = node["exercises"]
    check("重复题号解析不产生空题", len(exs) == 3, str(exs))
    check(
        "带题号的【答案】合并回选择题",
        exs[0]["content"] == "设 $x=1$，则 $x+1=$\n\nA. 1\n\nB. 2"
        and exs[0]["answer"] == "B"
        and exs[0]["solution_steps"] == "由 $x=1$ 可得 $x+1=2$。",
    )
    check(
        "转义题号保留题干并分离解答",
        exs[1]["content"] == "（本题满分10分）\n\n计算 $x$。"
        and exs[1]["solution_steps"] == "设 $x=1$，故结果为 1。",
    )
    check(
        "普通重复题号同样合并",
        exs[2]["content"] == "求 $y$。"
        and exs[2]["solution_steps"] == "由题意得 $y=2$。",
    )
def test_exampaper_english_cambridge() -> None:
    """无题型小节的 Cambridge 卷按顶层题号切分。"""
    from exercise_extractor import extract_exercise_from_markdown

    sections = list(extract_exercise_from_markdown(
        load_scenario("exampaper_english_cambridge.txt"),
        doc_type="exampaper", document_name="9709/11",
    ))
    check("Cambridge 英文卷产生单个 section", len(sections) == 1, str(sections))
    if not sections:
        return
    items = sections[0]["exercises"]
    check("Cambridge 顶层题号和页码", [e["number"] for e in items] == ["1", "2", "3"]
          and [e.get("pages") for e in items] == [[2, 2], [3, 3], [4, 4]], str(items))
    check("Cambridge 小问保留在题内", "(a)" in items[0]["content"]
          and "(b)" in items[0]["content"]
          and "9709/11/M/J/24" not in items[0]["content"], str(items[0]))


def test_exampaper_english_aqa() -> None:
    """AQA 物理卷按 Section 分组，零填充题号的小问不拆题。"""
    from exercise_extractor import extract_exercise_from_markdown

    sections = list(extract_exercise_from_markdown(
        load_scenario("exampaper_english_aqa.txt"),
        doc_type="exampaper", document_name="7408/1",
    ))
    check("AQA 英文 Section A/B", [s["title"] for s in sections] == ["Section A", "Section B"], str(sections))
    if len(sections) != 2:
        return
    a, b = [s["exercises"] for s in sections]
    check("AQA 结构题保留小问", [e["number"] for e in a] == ["1", "2", "3"]
          and "0 1 . 2" in a[0]["content"] and a[0]["pages"] == [2, 2]
          and a[2]["content"].startswith("0 3 . 1"), str(a))
    check("AQA Section B 为单选题", [e["number"] for e in b] == ["7", "8"]
          and all(e["question_type"] == "single_choice" for e in b)
          and b[0]["pages"] == [24, 24], str(b))


def test_exampaper_english_aqa_math() -> None:
    """AQA 数学的裸题号、单独数字选项和逐题选择分类。"""
    from exercise_extractor import extract_exercise_from_markdown

    sections = list(extract_exercise_from_markdown(
        load_scenario("exampaper_english_aqa_math.txt"),
        doc_type="exampaper", document_name="7357/1",
    ))
    check("AQA 数学无 Section 仍按题提取", len(sections) == 1, str(sections))
    if not sections:
        return
    items = sections[0]["exercises"]
    check("AQA 数学独立选项不切题", [e["number"] for e in items] == ["1", "2", "3"]
          and "\n2\n128" in items[0]["content"]
          and "2.5" in items[1]["content"]
          and items[2]["content"].startswith("The curve"), str(items))
    check("AQA 数学逐题识别单选", [e["question_type"] for e in items]
          == ["single_choice", "single_choice", "short_answer"], str(items))


def test_exampaper_english_ocr_gap() -> None:
    """真实 OCR 漏掉整页 Cambridge 题号时，按页码和后续题号补回。"""
    from exercise_extractor import extract_exercise_from_markdown

    sections = list(extract_exercise_from_markdown(
        load_scenario("exampaper_english_ocr_gap.txt"),
        doc_type="exampaper", document_name="9709/11",
    ))
    check("OCR 英文页无封面仍能识别", len(sections) == 1, str(sections))
    if not sections:
        return
    items = sections[0]["exercises"]
    check("OCR 缺号页恢复题号", [e["number"] for e in items] == ["1", "2", "3"]
          and [e.get("pages") for e in items] == [[1, 1], [2, 2], [3, 3]], str(items))
    check("OCR 缺号页题干归属", "The diagram shows" in items[1]["content"]
          and "The diagram shows" not in items[0]["content"]
          and "The coefficient" in items[2]["content"]
          and 'src="imgs/diagram.jpg"' in items[1]["content"]
          and 'src="imgs/blank.jpg"' not in items[2]["content"], str(items))


def test_exampaper_english_ocr_multi_gap() -> None:
    """整卷 OCR 连续漏号、跨页小问和卷末附加页。"""
    from exercise_extractor import extract_exercise_from_markdown

    sections = list(extract_exercise_from_markdown(
        load_scenario("exampaper_english_ocr_multi_gap.txt"),
        doc_type="exampaper", document_name="9709/11",
    ))
    check("OCR 整卷英文 section", len(sections) == 1, str(sections))
    if not sections:
        return
    items = sections[0]["exercises"]
    check("OCR 连续漏号恢复 1-11", [e["number"] for e in items]
          == [str(i) for i in range(1, 12)], str(items))
    if len(items) != 11:
        return
    check("OCR 跨页和卷末边界", [e["pages"] for e in items[4:]]
          == [[6, 6], [7, 7], [8, 8], [10, 11], [12, 12], [14, 14], [16, 17]]
          and "Additional page" not in items[-1]["content"]
          and "[Unabel]" not in items[6]["content"], str(items[4:]))


def test_figure_group() -> None:
    """配图按图注归组（含 (a)(b) 子标签）。"""
    from exercise_extractor.extraction import extract_figures

    _, figures = extract_figures(load_scenario("html_figure_group.txt"))
    check("配图归组含子标签", len(figures.get("图1-1", [])) == 4)


# 场景文件 → 测试函数注册表
SCENARIO_TESTS = {
    "textbook_fake_cache_page1.txt": test_textbook_fake_cache,
    "textbook_decimal_display_formula.txt": test_textbook_decimal_display_formula,
    "textbook_chapter_prefixed_numbers.txt": test_textbook_chapter_prefixed_numbers,
    "exercise_book_prefix.txt": test_exercise_book_prefix,
    "exercise_book_no_toc.txt": test_exercise_book_no_toc,
    "answers_split_sections.txt": test_answers_split,
    "exampaper_recent_paper.txt": test_exampaper_recent,
    "exampaper_old_paper.txt": test_exampaper_old,
    "exampaper_chinese_scored_questions.txt": (
        test_exampaper_chinese_scored_questions
    ),
    "exampaper_declared_multiple_subquestions.txt": (
        test_exampaper_declared_multiple_subquestions
    ),
    "exampaper_scoring_tables_and_instructions.txt": (
        test_exampaper_scoring_tables_and_instructions
    ),
    "exampaper_inline_jieda.txt": test_exampaper_inline_jieda,
    "exampaper_tail_answers.txt": test_exampaper_tail_answers,
    "exampaper_parenthesized_answers.txt": test_exampaper_parenthesized_answers,
    "exampaper_paren_headings.txt": test_exampaper_paren_headings,
    "exampaper_multi_choice_proof.txt": test_exampaper_multi_choice_proof,
    "exampaper_single_multi_choice.txt": test_exampaper_single_multi_choice,
    "exampaper_table_tail.txt": test_exampaper_table_tail,
    "exampaper_duplicate_number_solution.txt": test_exampaper_duplicate_number_solution,
    "exampaper_english_cambridge.txt": test_exampaper_english_cambridge,
    "exampaper_english_aqa.txt": test_exampaper_english_aqa,
    "exampaper_english_aqa_math.txt": test_exampaper_english_aqa_math,
    "exampaper_english_ocr_gap.txt": test_exampaper_english_ocr_gap,
    "exampaper_english_ocr_multi_gap.txt": test_exampaper_english_ocr_multi_gap,
    "html_figure_group.txt": test_figure_group,
}

# 多页场景的附属文件：由主场景测试函数自行加载，不单独分发
SECONDARY_SCENARIO_FILES = {"textbook_fake_cache_page2.txt"}
