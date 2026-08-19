"""场景化测试：每个 test_scenarios/*.txt 对应一个测试函数。

测试启动后依次扫描 test_scenarios 目录，按文件名经 SCENARIO_TESTS
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
            "解：解答一" in text and "答：答案二" in text,
        )
        with open(os.path.join(outdir, "exercises.json"), encoding="utf-8") as f:
            data = json.load(f)
        check(
            "exercises.json 含习题节点",
            data[0]["exercises"][0]["content"].startswith("第一题"),
        )


def test_exercise_book_prefix() -> None:
    """习题集“习题 N.”前缀归一。"""
    from exercise_extractor import exercise_book

    content = load_scenario("exercise_book_prefix.txt")
    check(
        "习题前缀归一",
        exercise_book._normalize_exercise_prefixes(content) == "1. a\n2. b\n",
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


def test_figure_group() -> None:
    """配图按图注归组（含 (a)(b) 子标签）。"""
    from exercise_extractor.extraction import extract_figures

    _, figures = extract_figures(load_scenario("html_figure_group.txt"))
    check("配图归组含子标签", len(figures.get("图1-1", [])) == 4)


# 场景文件 → 测试函数注册表
SCENARIO_TESTS = {
    "textbook_fake_cache_page1.txt": test_textbook_fake_cache,
    "exercise_book_prefix.txt": test_exercise_book_prefix,
    "exercise_book_no_toc.txt": test_exercise_book_no_toc,
    "answers_split_sections.txt": test_answers_split,
    "exampaper_recent_paper.txt": test_exampaper_recent,
    "exampaper_old_paper.txt": test_exampaper_old,
    "exampaper_inline_jieda.txt": test_exampaper_inline_jieda,
    "exampaper_tail_answers.txt": test_exampaper_tail_answers,
    "exampaper_paren_headings.txt": test_exampaper_paren_headings,
    "exampaper_multi_choice_proof.txt": test_exampaper_multi_choice_proof,
    "exampaper_single_multi_choice.txt": test_exampaper_single_multi_choice,
    "exampaper_table_tail.txt": test_exampaper_table_tail,
    "html_figure_group.txt": test_figure_group,
}

# 多页场景的附属文件：由主场景测试函数自行加载，不单独分发
SECONDARY_SCENARIO_FILES = {"textbook_fake_cache_page2.txt"}
