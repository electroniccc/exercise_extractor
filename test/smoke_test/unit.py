"""非场景单元测试（单行/字典结构的小断言，不抽成 txt）。"""

import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from .common import check, run_cli


def test_unit_boundaries() -> None:
    from exercise_extractor.parse_md_exercises import (
        filter_exercises_by_section,
        normalize_section,
        split_solutions_for_section,
        split_exercises,
    )

    check("空内容返回空列表", split_exercises("") == [])
    exs = split_exercises("## 习题\n\n### 小节\n")
    check(
        "纯标题内容存为 preamble",
        len(exs) == 1 and exs[0]["number"] == "__preamble__",
    )
    exs = split_exercises("1. 题目\n\n3. 下一题\n")
    check("单段题号递增", [e["number"] for e in exs] == ["1", "3"])
    exs = split_exercises("2.5 是正文\n\n3. 下一题\n")
    check(
        "2.5 按两段式题号处理（已知权衡）",
        exs and exs[0]["number"] == "2.5",
    )
    exs = split_exercises("1. 一\n\n4—6. 二\n\n7. 三\n")
    check("区间题号 4-6", any(e.get("number") == "4-6" for e in exs))
    exs = split_exercises("1.1.1 证明：a\n\n1.1.2 证明：b\n")
    check("多级题号", [e["number"] for e in exs] == ["1.1.1", "1.1.2"])
    e = split_exercises("1. 题目\n\n解 由题意可知…\n")[0]
    check("解+空格切分", e.get("solution_steps", "").startswith("由题意"))
    e = split_exercises("1. 设…\n\n证明：结论成立。\n\n证明. 由已知…\n")[0]
    check(
        "证明多标记取最后",
        "结论成立" in e["content"]
        and e.get("solution_steps", "").startswith("由已知"),
    )
    e = split_exercises("1.1.1 证明： (a) x\n")[0]
    check("纯证明题题干保留", e.get("solution_steps", "") == "")
    check(
        "目录与 OCR 区间横线归一化",
        normalize_section("习题 1–5") == normalize_section("习题1-5"),
    )
    check(
        "LaTeX 小节名与 Unicode 符号可归一比较",
        normalize_section(r"$\alpha$ 习题") == normalize_section("α 习题"),
    )
    exs = split_exercises("## 习题1-5\n1. 一\n2. 二\n")
    check(
        "区间横线差异不误删习题",
        len(filter_exercises_by_section(exs, "习题 1–5")) == 2,
    )
    from exercise_extractor.extraction import extract_exercises

    section = {
        "title": "习题 1–5",
        "has_exercise": True,
        "exercise_content": "<!-- page: 60 -->\n## 习题1-5\n1. 一\n2. 二\n",
        "boundary_page": 60,
        "children": [],
    }
    extract_exercises([section])
    check(
        "区间横线差异不触发边界页误截断",
        len(section.get("exercises", [])) == 2,
    )
    proof_only = split_exercises(
        "1. 试证明：结论成立。\n证明：这是题目要求。\n",
        check_section_end=False,
        split_solutions=False,
    )
    split_solutions_for_section(proof_only, "习题", doc_type="textbook")
    check(
        "普通课本节不把单个证明误切成解答",
        proof_only[0]["solution_steps"] == ""
        and "证明：这是题目要求" in proof_only[0]["content"],
    )
    example = split_exercises(
        "1. 求结果。\n证明：由定义可得结果。\n",
        check_section_end=False,
        split_solutions=False,
    )
    split_solutions_for_section(example, "补充例题", doc_type="textbook")
    check(
        "例题节允许单个证明标记作为解答",
        example[0]["solution_steps"] == "由定义可得结果。",
    )
    example_number = split_exercises(
        "例 1.2 设函数连续。\n解：由定义可得。\n",
        check_section_end=False,
        split_solutions=False,
    )
    split_solutions_for_section(example_number, "习题", doc_type="textbook")
    check(
        "例题编号触发解答策略",
        example_number[0]["number"] == "例1.2"
        and example_number[0]["solution_steps"] == "由定义可得。",
    )
    mixed = split_exercises(
        "1. 题一\n解：过程一\n\n2. 题二\n无解答。\n\n3. 题三\n解答：过程三\n",
        check_section_end=False,
        split_solutions=False,
    )
    split_solutions_for_section(mixed, "习题 1", doc_type="exercise_book")
    check(
        "习题集按整节多数证据分离解答",
        mixed[0]["solution_steps"] == "过程一"
        and mixed[1]["solution_steps"] == ""
        and mixed[2]["solution_steps"] == "过程三",
    )
    book_proofs = split_exercises(
        "1. 证明：结论一。\n\n证明. 过程一。\n\n"
        "2. 证明：结论二。\n\n证法一．过程二。\n",
        check_section_end=False,
        split_solutions=False,
    )
    split_solutions_for_section(book_proofs, "习题 4.3", doc_type="exercise_book")
    check(
        "习题集题尾证明与证法分离",
        book_proofs[0]["content"] == "证明：结论一。"
        and book_proofs[0]["solution_steps"] == "过程一。"
        and book_proofs[1]["content"] == "证明：结论二。"
        and book_proofs[1]["solution_steps"] == "过程二。",
    )
    check(
        "习题集题尾证明标记为证明题",
        all(e.get("question_type") == "proof" for e in book_proofs),
    )
    from exercise_extractor.exampaper import _split_section_exercises

    paper_proofs = _split_section_exercises(
        {
            "lines": [
                "1. 证明：结论一。",
                "证明. 过程一。",
                "2. 设 x。",
                "解：普通过程。",
            ],
            "question_type": "short_answer",
            "section_key": "三",
            "title": "三、解答题",
            "start_page": 1,
        }
    )
    check(
        "试卷题尾证明按题目单独标记",
        paper_proofs[0]["question_type"] == "proof"
        and paper_proofs[1]["question_type"] == "short_answer",
    )
    from exercise_extractor.parse_md_exercises import solution_starts_with_proof

    # 解析标记由试卷专用后处理拆分；这里验证原始“解析：证明”能被识别为
    # 证明型解答起点（完整流程会在 split_inline_answers 后写入 question_type）。
    check(
        "试卷解析以证明开头标记为证明题",
        solution_starts_with_proof("1. 题目。\n解析：证明这是证明过程。"),
    )
    from exercise_extractor.exampaper import _normalize_choice_markers

    check(
        "选择题选项标记统一为字母加点",
        _normalize_choice_markers("(A) 甲\n(B)．乙\nC) 丙\nD、丁")
        == "A. 甲\nB. 乙\nC. 丙\nD. 丁",
    )
    fill_numbered = _split_section_exercises(
        {
            "lines": ["11) 题11", "12）题12", "13) 题13", "4) 题14", "5）题15", "(6) 题16"],
            "question_type": "fill_in_blank",
            "section_key": "二、填空题",
            "title": "二、填空题",
            "start_page": 1,
        }
    )
    check(
        "填空题题号首位缺失时连续修复",
        [e["number"] for e in fill_numbered] == ["11", "12", "13", "14", "15", "16"],
    )
    escaped_number = _split_section_exercises(
        {
            "lines": [
                r"17\.（本题满分10分）",
                "",
                "计算 $x$。",
                "",
                "17. 解：",
                "过程。",
            ],
            "question_type": "short_answer",
            "section_key": "三",
            "title": "三、解答题",
            "start_page": 1,
        }
    )
    check(
        "试卷转义题号保留题干并合并重复解答题号",
        len(escaped_number) == 1
        and escaped_number[0]["content"] == "（本题满分10分）\n\n计算 $x$。"
        and escaped_number[0]["solution_steps"] == "过程。",
    )


def test_toc_and_boundaries() -> None:
    from exercise_extractor import exercise_book
    from exercise_extractor.extraction import build_section_tree, collect_pages_to_ocr
    from exercise_extractor.toc_detect import (
        is_answers_title,
        is_exercise_title,
        is_skippable_title,
        should_use_tail_search,
    )

    toc = [
        {"title": "Preface", "page_num": "x", "children": []},
        {
            "title": "1 Functions",
            "page_num": 7,
            "children": [
                {"title": "1.1 Ex", "page_num": 8, "children": []},
                {"title": "1.2 Ex", "page_num": 20, "children": []},
            ],
        },
    ]
    sections = build_section_tree(toc, doc_type="textbook", doc_page_count=500)
    check("罗马数字页码不崩溃", len(sections) >= 1)

    no_toc = exercise_book.build_exercise_book_sections([], 100)
    check(
        "习题集无目录→整本节点",
        len(no_toc) == 1
        and no_toc[0].get("no_toc")
        and no_toc[0]["exercise_page_range"] == [1, 101],
    )

    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "page_1"))
        with open(os.path.join(tmp, "page_1", "page.md"), "w") as stream:
            stream.write("旧缓存")
        pages = collect_pages_to_ocr(
            [{"has_exercise": True, "exercise_page_range": [1, 3]}], tmp
        )
        check("缺少 READY 的旧缓存重新 OCR", pages == [1, 2], f"got {pages}")
        with open(os.path.join(tmp, "page_1", "page.json"), "w") as stream:
            json.dump({"parsing_res_list": []}, stream)
        with open(os.path.join(tmp, "page_1", "READY"), "w") as stream:
            stream.write("ready\n")
        pages = collect_pages_to_ocr(
            [{"has_exercise": True, "exercise_page_range": [1, 3]}], tmp
        )
        check("完整发布的页面命中缓存", pages == [2], f"got {pages}")

    check("答案标题识别", is_answers_title("第3章 习题答案"))
    check(
        "习题/补充例题识别",
        is_exercise_title("补充例题") and is_exercise_title("chapter 3 problems"),
    )
    check(
        "小结/参考书跳过",
        is_skippable_title("本章小结") and is_skippable_title("主要参考书"),
    )
    check("普通习题不跳过", not is_skippable_title("习题 1-1"))
    toc_no_ex = [
        {
            "title": "第1章",
            "page_num": 1,
            "children": [
                {"title": "1.1", "page_num": 1, "children": []},
                {"title": "1.2", "page_num": 5, "children": []},
            ],
        }
    ]
    check(
        "无习题目录→启用节尾搜索",
        should_use_tail_search(toc_no_ex, "textbook"),
    )
    toc_has_ex = [
        {
            "title": "第1章",
            "page_num": 1,
            "children": [
                {"title": "1.1", "page_num": 1, "children": []},
                {"title": "习题 1-1", "page_num": 3, "children": []},
                {"title": "1.2", "page_num": 5, "children": []},
                {"title": "习题 1-2", "page_num": 8, "children": []},
            ],
        }
    ]
    check(
        "习题目录已覆盖→关闭节尾搜索",
        not should_use_tail_search(toc_has_ex, "textbook"),
    )


def test_answers_match() -> None:
    import exercise_extractor.answers as answers

    from exercise_extractor.answers import (
        _effective_answer_count,
        extract_chapter_number,
        fill_exercise_answers,
        match_answer_sections,
    )

    check("章号提取", extract_chapter_number("第一篇 › 第1章") == 1)
    check("§ 标签提取", extract_chapter_number("§2.3") == 2)
    check("习题号提取", extract_chapter_number("习题 9-2") == 9)
    check("空标题章号", extract_chapter_number(None) is None)
    secs = [
        {
            "title": "第1章 习题",
            "has_exercise": True,
            "children": [],
            "exercises": [
                {
                    "number": "1",
                    "content": "一",
                    "answer": "内联答案",
                    "solution_steps": "",
                },
                {"number": "2", "content": "二", "answer": "", "solution_steps": ""},
                {"number": "3", "content": "三", "answer": "", "solution_steps": ""},
                {"number": "4", "content": "四", "answer": "", "solution_steps": ""},
                {"number": "5", "content": "五", "answer": "", "solution_steps": ""},
            ],
        }
    ]
    ans_match = [
        {
            "title": "第1章",
            "answers": [
                {"number": "1", "content": "答一", "separator": "."},
                {"number": "2", "content": "答二", "separator": "."},
                {"number": "3", "content": "答三", "separator": "."},
                {"number": "4-5", "content": "答四五", "separator": "."},
            ],
        }
    ]
    match_answer_sections(ans_match, secs)
    check(
        "答案块匹配+题数校验",
        ans_match[0].get("matched_title") == "第1章 习题"
        and ans_match[0].get("count_match") is True
        and _effective_answer_count(ans_match[0]["answers"]) == 5,
    )
    fill_exercise_answers(secs)
    exs = secs[0]["exercises"]
    check("逐题回填", exs[1]["answer"] == "答二")
    check(
        "区间答案展开回填",
        exs[3]["answer"] == "答四五" and exs[4]["answer"] == "答四五",
    )
    check("已有内联答案不被覆盖", exs[0]["answer"] == "内联答案")

    from exercise_extractor import match_answers

    mixed_sections = [
        {
            "title": "思考题",
            "path": "第一章/思考题",
            "has_exercise": True,
            "children": [],
            "exercises": [
                {"number": str(i), "original_number": str(i), "content": f"思考{i}", "answer": ""}
                for i in (1, 2)
            ],
        },
        {
            "title": "习题",
            "path": "第一章/习题",
            "has_exercise": True,
            "children": [],
            "exercises": [
                {"number": str(i), "original_number": str(i), "content": f"习题{i}", "answer": ""}
                for i in (1, 2, 3)
            ],
        },
    ]
    chapter_answers = [
        {
            "title": "第一章",
            "answers": [
                {"number": str(i), "content": f"习题答案{i}"} for i in (1, 2)
            ],
        }
    ]
    from copy import deepcopy

    generic_answer_sections = deepcopy(mixed_sections)
    match_answer_sections(
        chapter_answers, mixed_sections, source_title="习题答案"
    )
    fill_exercise_answers(mixed_sections)
    check(
        "习题答案不匹配到先出现的思考题",
        mixed_sections[0]["exercises"][0]["answer"] == ""
        and mixed_sections[0].get("answer_match", {}).get("matched") is not True
        and mixed_sections[1]["exercises"][0]["answer"] == "习题答案1"
        and mixed_sections[1]["answer_match"]["matched"] is True,
    )
    generic_answers = [
        {
            "title": "第一章",
            "answers": [
                {"number": str(i), "content": f"参考答案{i}"} for i in (1, 2)
            ],
        }
    ]
    match_answer_sections(
        generic_answers, generic_answer_sections, source_title="参考答案"
    )
    check(
        "泛化参考答案同章优先匹配常规习题",
        generic_answer_sections[0].get("answer_match", {}).get("matched") is not True
        and generic_answer_sections[1].get("answer_match", {}).get("matched") is True,
    )

    stream_sections = deepcopy(generic_answer_sections)
    stream_targets = answers.collect_exercise_targets(stream_sections)
    stream_used: set[int] = set()
    stream_answers = [
        {
            "title": "第一章",
            "answers": [
                {"number": str(i), "content": f"流式答案{i}"} for i in (1, 2)
            ],
        }
    ]
    answers.match_section_answers(
        stream_answers,
        stream_targets,
        0,
        stream_used,
        source_title="习题答案",
    )
    answers.match_section_answers(
        stream_answers,
        stream_targets,
        1,
        stream_used,
        source_title="习题答案",
    )
    check(
        "逐节答案匹配不会把习题答案回填到思考题",
        stream_sections[0]["exercises"][0]["answer"] == ""
        and stream_sections[1]["exercises"][0]["answer"] == "流式答案1"
        and stream_used == {0},
    )

    # 流式/公共 JSON 匹配也必须把答案章节标题传到同一套类型约束中。
    mixed_public = [
        {
            "document_name": "测试书",
            "title": title,
            "path": f"第一章/{title}",
            "has_exercise": True,
            "exercises": [
                {
                    "number": str(i),
                    "original_number": str(i),
                    "content": f"{title}{i}",
                    "answer": "",
                    "solution_steps": "",
                    "question_type": "short_answer",
                    "pages": [],
                }
                for i in range(1, count + 1)
            ],
        }
        for title, count in (("思考题", 2), ("习题", 3))
    ]
    public_matched = match_answers(
        mixed_public,
        {
            "title": "参考答案",
            "answer_sections": chapter_answers,
        },
    )
    check(
        "公共 match_answers 泛化答案默认不匹配思考题",
        public_matched[0]["exercises"][0]["answer"] == ""
        and public_matched[0]["answer_match"]["matched"] is False
        and public_matched[1]["exercises"][0]["answer"] == "习题答案1",
    )

    public_section = {
        "document_name": "测试书",
        "title": "第1章习题",
        "path": "第一章/第1章习题",
        "has_exercise": True,
        "exercise_page_range": [1, 2],
        "implicit": False,
        "tail_search_used": False,
        "exercises": [
            {
                "number": "1",
                "content": "题目",
                "answer": "",
                "solution_steps": "",
                "question_type": "short_answer",
                "pages": [],
            }
        ],
    }
    answer_section = {
        "title": "习题答案",
        "answer_sections": [
            {"title": "第1章", "answers": [{"number": "1", "content": "答案"}]}
        ],
    }
    matched = match_answers([public_section], answer_section)
    check(
        "公共 match_answers 返回新 section list",
        matched[0]["exercises"][0]["answer"] == "答案"
        and matched[0]["answer_match"]["count_match"] is True
        and "children" not in matched[0]
        and public_section["exercises"][0]["answer"] == "",
    )


def test_html_tables() -> None:
    from exercise_extractor.html_tables import (
        detect_html_table,
        html_table_to_markdown,
    )

    good = "<table><tr><td>$x$</td><td>y</td></tr></table>"
    bad = "<table><tr><td>a<td>b</tr></table>"
    check("HTML 表格检测(闭合)", detect_html_table(good))
    check("HTML 表格检测(未闭合)", not detect_html_table(bad))
    md = html_table_to_markdown(good)
    check("HTML 表格转 markdown", "| $x$ | y |" in md)


def test_exercise_book_sections() -> None:
    from exercise_extractor import exercise_book

    sections_eb = exercise_book.build_exercise_book_sections(
        [
            {
                "title": "第1章",
                "page_num": 1,
                "children": [
                    {"title": "1.1", "page_num": 1, "children": []},
                    {"title": "1.2", "page_num": 4, "children": []},
                ],
            }
        ],
        10,
    )
    check(
        "习题集目录→叶子节",
        len(sections_eb) == 1 and len(sections_eb[0]["children"]) == 2,
    )
    check(
        "习题集末节延伸到文末",
        sections_eb[0]["children"][1]["exercise_page_range"] == [4, 11],
    )


def test_exampaper_build_node() -> None:
    from exercise_extractor import exampaper

    sections_ep = exampaper.build_exampaper_sections("2023年考研数学（一）真题", 4)
    check(
        "试卷整卷节点",
        len(sections_ep) == 1
        and sections_ep[0]["exampaper"]
        and sections_ep[0]["exercise_page_range"] == [1, 5],
    )


def test_exampaper_output_sections() -> None:
    from exercise_extractor import exampaper

    node = {
        "title": "2025 真题",
        "has_exercise": True,
        "exampaper": True,
        "children": [],
        "exercise_page_range": [1, 5],
        "exercise_content": (
            "一、选择题\n1. 题一\n\n"
            "二、填空题\n11. 题二\n\n"
            "三、解答题\n17. 题三\n"
        ),
    }
    exampaper.extract_exercises([node])
    check(
        "试卷按题型生成独立 section",
        not node["has_exercise"]
        and [child["title"] for child in node["children"]]
        == ["一、选择题", "二、填空题", "三、解答题"]
        and [len(child["exercises"]) for child in node["children"]]
        == [1, 1, 1],
    )
    bare_heading = {
        "title": "南京大学 851 真题",
        "has_exercise": True,
        "exampaper": True,
        "children": [],
        "exercise_page_range": [1, 4],
        "exercise_content": (
            "## 南京大学\n\n（1）第一道题\n\n（2）第二道题\n\n"
            "二、已知连续时间信号 f(t)。\n（1）求原信号。\n（2）求变换。\n\n"
            "三、已知系统函数 H(s)，求系统响应。\n"
        ),
    }
    exampaper.extract_exercises([bare_heading])
    check(
        "试卷无题型标题时识别中文序号题目",
        [child["title"] for child in bare_heading["children"]]
        == ["一、简答题", "二", "三"]
        and [len(child["exercises"]) for child in bare_heading["children"]]
        == [2, 1, 1],
    )


def test_tail_search_title_stays_out_of_json() -> None:
    import tempfile

    from exercise_extractor.postprocess import write_outputs

    section = {
        "title": "1.1 Review of Functions",
        "has_exercise": True,
        "implicit": True,
        "tail_search_used": True,
        "children": [],
        "exercise_page_range": [17, 19],
        "exercises": [
            {
                "number": "1",
                "content": "题目",
                "answer": "",
                "solution_steps": "",
            }
        ],
    }
    with tempfile.TemporaryDirectory() as out_dir, tempfile.TemporaryDirectory() as md_dir:
        write_outputs([section], out_dir, md_dir)
        with open(os.path.join(out_dir, "17_1.1_Review_of_Functions.json"), encoding="utf-8") as f:
            payload = json.load(f)
        check(
            "节尾搜索 title 不写进 section JSON",
            payload["title"] == "1.1 Review of Functions"
            and payload["path"] == "1.1 Review of Functions"
            and payload["tail_search_used"] is True,
            payload.get("title"),
        )
        check(
            "节尾搜索后缀只保留在 markdown 文件名",
            os.path.exists(
                os.path.join(
                    out_dir,
                    "17_1.1_Review_of_Functions（节尾习题）.md",
                )
            ),
        )


def test_api_contract() -> None:
    """extract_exercise 返回迭代器，且入参校验在调用时立即执行。"""
    import inspect

    from exercise_extractor.api import (
        extract_exercise,
        extract_exercise_from_markdown,
    )

    params = inspect.signature(extract_exercise).parameters
    check(
        "API 暴露统一 OCR engine/device 参数",
        all(
            k in params
            for k in ("ocr_device", "ocr_engine")
        ),
    )
    check(
        "API 不再暴露独立 toc engine/device 参数",
        "toc_device" not in params and "toc_engine" not in params,
    )
    check("API 不再要求 source_type", "source_type" not in params)
    check(
        "API 不保留旧版 vl 参数",
        "vl_server_url" not in params and "vl_model" not in params,
    )
    check(
        "API 暴露 toc_forge vLLM 配置",
        all(k in params for k in ("toc_vllm_name", "toc_api_base_url", "toc_api_key")),
    )
    check(
        "API 支持图片输入",
        "image" in __import__(
            "exercise_extractor.api", fromlist=["SUPPORTED_SOURCE_TYPES"]
        ).SUPPORTED_SOURCE_TYPES,
    )
    markdown_sections = list(
        extract_exercise_from_markdown(
            "## 第一节 极限\n\n1. 求极限。\n\n答：0\n\n"
            "## 第二节 连续\n\n1. 判断连续性。\n",
            document_name="测试教材",
        )
    )
    check(
        "Markdown API 按标题返回独立 section",
        [item["title"] for item in markdown_sections] == ["第一节 极限", "第二节 连续"]
        and [len(item["exercises"]) for item in markdown_sections] == [1, 1]
        and markdown_sections[0]["document_name"] == "测试教材",
        str(markdown_sections),
    )
    check(
        "Markdown API 保留解答字段与纯题目 content",
        markdown_sections[0]["exercises"][0]["answer"] == "0"
        and "##" not in markdown_sections[0]["content"]["original"],
    )
    markdown_with_answers = list(
        extract_exercise_from_markdown(
            "## 习题\n\n1. 求结果。\n\n## 答案:\n\n"
            "一、填空题\n\n1. 0\n\n2. 1\n",
            doc_type="exercise_book",
        )
    )
    check(
        "Markdown API 将答案标题返回为答案 section",
        len(markdown_with_answers) == 2
        and markdown_with_answers[-1].get("is_answers") is True
        and markdown_with_answers[-1]["title"] == "答案:"
        and [len(block["answers"]) for block in markdown_with_answers[-1]["answer_sections"]]
        == [2],
        str(markdown_with_answers),
    )
    markdown_paper_sections = list(
        extract_exercise_from_markdown(
            "# 一、选择题\n\n1. 请选择正确答案。\n(A) 甲\n(B) 乙\n\n"
            "# 二、填空题\n\n2. 填空。\n",
            doc_type="exampaper",
            title="测试试卷",
        )
    )
    check(
        "Markdown API 复用试卷题型 section 切分",
        [item["title"] for item in markdown_paper_sections] == ["一、选择题", "二、填空题"]
        and [item["exercises"][0]["question_type"] for item in markdown_paper_sections]
        == ["single_choice", "fill_in_blank"],
        str(markdown_paper_sections),
    )
    from exercise_extractor.api import _prepare_source
    from exercise_extractor.ocr import build_sections
    from PIL import Image

    image_path = os.path.join(tempfile.gettempdir(), "exercise_extractor_test.png")
    Image.new("RGB", (32, 32), "white").save(image_path)
    try:
        image_args = SimpleNamespace(
            input=image_path,
            source_type="image",
            model_dir="/tmp/models",
            ocr_cache="/tmp/ocr-cache",
            doc_type="textbook",
        )
        image_nodes = build_sections(image_args)
        check(
            "图片输入建立单页习题节点",
            len(image_nodes) == 1
            and image_nodes[0]["exercise_page_range"] == [1, 2]
            and image_nodes[0]["has_exercise"],
        )
    finally:
        os.unlink(image_path)

    image_path2 = os.path.join(tempfile.gettempdir(), "exercise_extractor_test_2.png")
    Image.new("RGB", (32, 32), "white").save(image_path)
    Image.new("RGB", (48, 48), "white").save(image_path2)
    try:
        with tempfile.TemporaryDirectory() as image_cache:
            prepared, kind, cleanup = _prepare_source(
                [image_path, image_path2], image_cache
            )
            import pymupdf

            image_doc = pymupdf.open(prepared)
            prepared_again, kind_again, cleanup_again = _prepare_source(
                [image_path, image_path2], image_cache
            )
            check(
                "多图片输入合成为连续页面并命中缓存",
                kind == kind_again == "image"
                and image_doc.page_count == 2
                and prepared_again == prepared
                and not cleanup
                and not cleanup_again,
            )
            image_doc.close()
    finally:
        if os.path.exists(image_path):
            os.unlink(image_path)
        if os.path.exists(image_path2):
            os.unlink(image_path2)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake minimal")
        pdf = f.name
    try:
        gen = extract_exercise(pdf, model_dir="/tmp/models")
        check(
            "extract_exercise 返回迭代器",
            hasattr(gen, "__next__"),
            type(gen).__name__,
        )
        gen.close()
    finally:
        os.unlink(pdf)
    try:
        extract_exercise("/nonexistent.pdf", model_dir="/tmp/models")
        check("extract_exercise 入参校验即时抛错", False)
    except FileNotFoundError:
        check("extract_exercise 入参校验即时抛错", True)

    from unittest.mock import patch

    internal_nodes = [
        {
            "title": "第一章",
            "children": [
                {
                    "title": "习题一",
                    "has_exercise": True,
                    "exercise_page_range": [3, 4],
                    "_section_path": "第一章/习题一",
                    "exercises": [
                        {
                            "number": "1",
                            "content": "题目",
                            "answer": "",
                            "solution_steps": "",
                            "section": "第二章 随机变量及其分布",
                        }
                    ],
                }
            ],
        }
    ]
    no_exercise_node = {
        "title": "空习题节",
        "has_exercise": True,
        "exercises": [],
        "_section_path": "第一章/空习题节",
    }
    answer_node = {
        "title": "习题答案",
        "is_answers": True,
        "answer_sections": [],
        "_section_path": "答案/习题答案",
    }
    with patch(
        "exercise_extractor.api.iter_sections",
        return_value=iter(
            [
                no_exercise_node,
                answer_node,
                internal_nodes[0]["children"][0],
            ]
        ),
    ):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake minimal")
            public_pdf = f.name
        try:
            sections = list(extract_exercise(public_pdf, model_dir="/tmp/models"))
        finally:
            os.unlink(public_pdf)
    check(
        "extract_exercise 过滤空 section 并保留答案 section",
        len(sections) == 2
        and sections[0]["is_answers"] is True
        and sections[1]["path"] == "第一章/习题一"
        and "children" not in sections[1],
    )
    check(
        "section JSON content 不重复携带小节标题",
        not sections[1]["content"]["original"].lstrip().startswith("## "),
    )


def test_exercise_bbox() -> None:
    """PaddleOCR parsing block 能映射为带页码的题目级 bbox。"""
    from exercise_extractor.exercise_bbox import (
        _normalize_match_text,
        attach_exercise_bboxes,
    )
    from exercise_extractor.latex_text import latex_to_unicode
    from exercise_extractor.ocr import _save_ocr_result
    from exercise_extractor.public_schema import public_section_from_node

    check(
        "pylatexenc 将 LaTeX 数学命令转换为 Unicode",
        "α" in latex_to_unicode(r"$\alpha + \beta$"),
    )
    check(
        "bbox 文本匹配统一 LaTeX 与 Unicode 数学符号",
        _normalize_match_text(r"速度 $\alpha$") == _normalize_match_text("速度 α"),
    )

    with tempfile.TemporaryDirectory() as cache_dir:
        raw_result = Mock()

        def save_markdown(*, save_path: str, **_kwargs) -> None:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            with open(save_path, "w", encoding="utf-8") as file:
                file.write("1. 题目")

        def save_json(*, save_path: str, **_kwargs) -> None:
            with open(save_path, "w", encoding="utf-8") as file:
                json.dump({"parsing_res_list": []}, file)

        raw_result.save_to_markdown.side_effect = save_markdown
        raw_result.save_to_json.side_effect = save_json
        _save_ocr_result(raw_result, 2, cache_dir, "PaddleOCR-VL")
        check(
            "单页 OCR 同时缓存 Markdown 和原始 JSON",
            os.path.isfile(os.path.join(cache_dir, "page_2", "page.md"))
            and os.path.isfile(os.path.join(cache_dir, "page_2", "page.json"))
            and os.path.isfile(os.path.join(cache_dir, "page_2", "READY")),
        )

        page_dir = os.path.join(cache_dir, "page_3")
        os.makedirs(page_dir)
        with open(os.path.join(page_dir, "page.md"), "w", encoding="utf-8") as file:
            file.write("1. 求函数 $f(x)=x^2$ 的导数。")
        with open(os.path.join(page_dir, "page.json"), "w", encoding="utf-8") as file:
            json.dump(
                {
                    "parsing_res_list": [
                        {
                            "block_content": "1. 求函数 $f(x)=x^2$ 的导数。",
                            "block_bbox": [10, 20, 300, 90],
                        },
                        {
                            "block_content": "第二节 无关正文",
                            "block_bbox": [10, 100, 300, 140],
                        },
                    ]
                },
                file,
                ensure_ascii=False,
            )
        with open(os.path.join(page_dir, "READY"), "w", encoding="utf-8") as file:
            file.write("ready\n")
        node = {
            "title": "习题一",
            "has_exercise": True,
            "exercise_page_range": [3, 4],
            "exercises": [
                {
                    "number": "1",
                    "content": "求函数 $f(x)=x^2$ 的导数。",
                    "answer": "",
                    "solution_steps": "",
                    "pages": [3, 3],
                }
            ],
        }
        attach_exercise_bboxes([node], cache_dir)
        expected = [{"page": 3, "bbox": [10.0, 20.0, 300.0, 90.0]}]
        check("习题文本映射 OCR bbox", node["exercises"][0]["bbox"] == expected)
        public = public_section_from_node(node, document_name="测试书")
        check(
            "公共 Exercise JSON 保留固定 bbox 字段",
            public is not None and public["exercises"][0]["bbox"] == expected,
        )

        unmatched = {
            "title": "旧缓存",
            "has_exercise": True,
            "exercise_page_range": [4, 5],
            "exercises": [{"number": "1", "content": "题目", "pages": [4, 4]}],
        }
        attach_exercise_bboxes([unmatched], cache_dir)
        check(
            "缓存未完整发布时 bbox 为空",
            unmatched["exercises"][0]["bbox"] == [],
        )


def test_ocr_cache_concurrency() -> None:
    """相同 PDF 并发提取只生产一次，并且只发布完整页面缓存。"""
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import threading
    import time
    from unittest.mock import patch

    import pymupdf

    from exercise_extractor.ocr import _save_ocr_result, ocr_pages
    from exercise_extractor.ocr_cache import page_cache_is_ready
    from exercise_extractor.utils import compute_file_hash

    with tempfile.TemporaryDirectory() as cache_dir:
        pdf_path = os.path.join(cache_dir, "same.pdf")
        document = pymupdf.open()
        document.new_page()
        document.save(pdf_path)
        document.close()
        with open(pdf_path, "rb") as stream:
            expected_hash = hashlib.sha256(stream.read()).hexdigest()[:32]
        check(
            "PDF 缓存 hash 使用 32 位",
            compute_file_hash(pdf_path) == expected_hash
            and len(compute_file_hash(pdf_path)) == 32,
        )

        markdown_path = os.path.join(cache_dir, expected_hash, "markdown")
        sections = [{"has_exercise": True, "exercise_page_range": [1, 2]}]
        args = SimpleNamespace(input=pdf_path, vl_model="PaddleOCR-VL")

        class Result:
            def save_to_markdown(self, *, save_path: str, **_kwargs) -> None:
                with open(save_path, "w", encoding="utf-8") as stream:
                    stream.write("1. 并发题目")

            def save_to_json(self, *, save_path: str, **_kwargs) -> None:
                with open(save_path, "w", encoding="utf-8") as stream:
                    json.dump({"parsing_res_list": []}, stream)

        class Pipeline:
            def __init__(self) -> None:
                self.calls = 0
                self.guard = threading.Lock()

            def predict_iter(self, images):
                with self.guard:
                    self.calls += 1
                time.sleep(0.1)
                return iter(Result() for _ in images)

        pipeline = Pipeline()
        barrier = threading.Barrier(2)

        def run() -> None:
            barrier.wait()
            ocr_pages(args, sections, markdown_path, pipeline)

        with patch("exercise_extractor.ocr.image_from_page", return_value=object()):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run) for _ in range(2)]
                for future in futures:
                    future.result()
        check(
            "相同 PDF 并发 OCR 只生产一次",
            pipeline.calls == 1 and page_cache_is_ready(markdown_path, 1),
            f"calls={pipeline.calls}",
        )

        bad_result = Mock()

        def write_markdown(*, save_path: str, **_kwargs) -> None:
            with open(save_path, "w", encoding="utf-8") as stream:
                stream.write("2. 半成品")

        bad_result.save_to_markdown.side_effect = write_markdown
        bad_result.save_to_json.side_effect = ValueError("invalid json")
        try:
            _save_ocr_result(bad_result, 2, markdown_path, "PaddleOCR-VL")
            failed_atomically = False
        except ValueError:
            failed_atomically = not os.path.exists(
                os.path.join(markdown_path, "page_2")
            )
        check("JSON 写入失败不发布 Markdown 半成品", failed_atomically)


def test_llm_review_contract() -> None:
    """LLM 审校接口只改文本并按题目追加状态，工具轮次可控。"""
    from exercise_extractor.review import (
        DEFAULT_REVIEW_BASE_URL,
        DEFAULT_REVIEW_MODEL,
        review_basic,
        review_markdown_basic,
        review_markdown_with_tools,
    )

    check("审校默认模型", DEFAULT_REVIEW_MODEL == "deepseek-v4-flash-0731")
    check(
        "审校默认地址与目录识别一致",
        DEFAULT_REVIEW_BASE_URL
        == "https://ws-alorrjihwnhn7hw7.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    )

    content = "*1.* 题目，内容\n\n解：$x$\n"
    basic_client = Mock()
    basic_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="*1.* 题目，内容\n\n解：$x$\n",
                    tool_calls=[],
                )
            )
        ]
    )
    reviewed = review_markdown_basic(
        content, "测试书", "习题1", client=basic_client
    )
    check("基础审校保留题目结构", "*1.* 题目，内容" in reviewed)
    check("基础审校不追加逐题状态", "审校状态：" not in reviewed)
    check(
        "基础审校显式关闭思考模式",
        basic_client.chat.completions.create.call_args.kwargs["extra_body"]
        == {"enable_thinking": False},
    )
    usage_events = []
    usage_client = Mock()
    usage_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=7),
    )
    review_markdown_basic(
        content,
        "测试书",
        "习题1",
        client=usage_client,
        usage_callback=usage_events.append,
    )
    check(
        "基础审校上报 token 用量",
        len(usage_events) == 1
        and usage_events[0].operation == "review.basic"
        and usage_events[0].input_tokens == 12
        and usage_events[0].output_tokens == 7,
    )
    section = {
        "document_name": "测试书",
        "title": "习题1",
        "path": "第一章/习题1",
        "has_exercise": True,
        "exercise_page_range": [1, 2],
        "implicit": False,
        "tail_search_used": False,
        "exercises": [
            {
                "number": "1",
                "content": "题目，内容",
                "answer": "",
                "solution_steps": "$x$",
                "question_type": "short_answer",
                "pages": [],
            }
        ],
    }
    reviewed_section = review_basic(section, client=basic_client)
    check(
        "公共 review_basic 返回 section JSON",
        isinstance(reviewed_section, dict)
        and reviewed_section["path"] == "第一章/习题1"
        and "children" not in reviewed_section
        and reviewed_section["content"]["basic_review"],
    )
    from exercise_extractor.review import review_basic_exercise

    reviewed_exercise = review_basic_exercise(
        {**section, "document_name": "测试书"},
        [0],
        client=basic_client,
    )
    check(
        "公共 review_basic_exercise 返回单题 JSON",
        isinstance(reviewed_exercise, list)
        and reviewed_exercise[0]["number"] == "1"
        and reviewed_exercise[0]["content"] == "题目，内容",
    )

    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(
            name="sympy_equivalent",
            arguments=json.dumps(
                {"expression_a": "x^2-1", "expression_b": "(x-1)*(x+1)"}
            ),
        ),
    )
    tool_client = Mock()
    tool_client.chat.completions.create.side_effect = [
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, tool_calls=[tool_call])
                )
            ]
        ),
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "markdown": content,
                                "statuses": [{"status": "无需修复"}],
                            }
                        ),
                        tool_calls=[],
                    )
                )
            ]
        ),
    ]
    reviewed = review_markdown_with_tools(
        content,
        "测试书",
        "习题1",
        client=tool_client,
        max_rounds=3,
        strict=True,
    )
    check("工具审校调用 SymPy", tool_client.chat.completions.create.call_count == 2)
    check("工具审校追加状态", "审校状态：无需修复" in reviewed)


def test_review_removes_supplemented_answers() -> None:
    """公共 basic review 删除模型给无答案题补出的解/答段。"""
    from exercise_extractor.review import review_basic

    content = (
        "*1.* 求 $x$。\n\n"
        "*2.* 求 $y$。\n\n解：原解答\n\n"
        "*3.* 求 $z$。\n\n答：原答案\n"
    )
    revised = (
        "*1.* 求 $x$。\n\n答：模型根据题目补出的答案\n\n"
        "*2.* 求 $y$。\n\n解：原解答（格式修复）\n\n"
        "答：模型补出的答案\n\n"
        "*3.* 求 $z$。\n\n答：原答案（格式修复）\n"
    )
    client = Mock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=revised,
                    tool_calls=[],
                )
            )
        ]
    )
    section = {
        "document_name": "测试书",
        "title": "习题1",
        "path": "习题1",
        "has_exercise": True,
        "exercise_page_range": [1, 2],
        "exercises": [
            {"number": "1", "content": "求 $x$。", "answer": "", "solution_steps": ""},
            {"number": "2", "content": "求 $y$。", "answer": "", "solution_steps": "原解答"},
            {"number": "3", "content": "求 $z$。", "answer": "原答案", "solution_steps": ""},
        ],
    }
    reviewed = review_basic(section, client=client)
    visible = reviewed["content"]["basic_review"]
    check("删除无答案题的模型补答", "模型根据题目补出的答案" not in visible)
    check("删除部分答案题的模型补答", "模型补出的答案" not in visible)
    check("保留原题已有解答", "原解答（格式修复）" in visible)
    check("保留原题已有答案", "原答案（格式修复）" in visible)
    check(
        "删除补答后仍保留题号",
        all(visible.count(f"*{number}.*") == 1 for number in (1, 2, 3)),
    )


def test_review_keeps_partial_tool_result() -> None:
    """工具轮达到上限仍保留此前返回的结构合法修复。"""
    from exercise_extractor.review import remove_review_statuses, review_markdown_with_tools

    content = "*1.* 原题目\n\n解：原解答\n"
    partial = "*1.* 已完成格式修复的题目\n\n解：原解答\n"
    tool_call = SimpleNamespace(
        id="call-partial",
        function=SimpleNamespace(
            name="sympy_simplify",
            arguments=json.dumps({"expression": "x^2 - 1"}),
        ),
    )
    client = Mock()
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=json.dumps(
                        {
                            "markdown": partial,
                            "statuses": [{"status": "已修复"}],
                        },
                        ensure_ascii=False,
                    ),
                    tool_calls=[tool_call],
                )
            )
        ]
    )
    client.chat.completions.create.side_effect = [response, response]
    reviewed = review_markdown_with_tools(
        content,
        "测试书",
        "习题1",
        client=client,
        max_rounds=2,
    )
    visible = remove_review_statuses(reviewed)
    check("达到轮数上限保留部分修复", "已完成格式修复的题目" in visible)
    check("部分修复保留状态", "审校状态：已修复" in reviewed)


def test_cli_review_skips_answer_nodes() -> None:
    """CLI 审校只处理习题节点，答案章节不重复调用 LLM。"""
    from unittest.mock import patch

    from exercise_extractor import main as cli_main

    sections = [
        {
            "title": "习题 1-1",
            "has_exercise": True,
            "children": [],
            "exercises": [
                {
                    "number": "1",
                    "content": "求 $x$。",
                    "answer": "$1$",
                    "solution_steps": "",
                }
            ],
        },
        {
            "title": "习题答案",
            "is_answers": True,
            "children": [],
            "answer_sections": [{"title": "第1章", "answers": []}],
        },
    ]
    args = SimpleNamespace(
        input="/tmp/book.pdf",
        review_base_url=None,
        review_api_key=None,
        review_model="qwen3.7-max",
        review_max_rounds=3,
    )
    calls = []

    def fake_basic(section, *rest, **kwargs):
        calls.append("basic")
        reviewed = dict(section)
        reviewed["content"] = {
            "original": section["content"]["original"],
            "basic_review": section["content"]["original"],
            "tool_review": section["content"]["original"],
        }
        return reviewed

    def fake_tools(content, *rest, **kwargs):
        calls.append("tools")
        return content

    completed = []
    with patch.object(cli_main, "review_basic", fake_basic), patch.object(
        cli_main, "review_markdown_with_tools", fake_tools
    ):
        cli_main._review_sections(
            sections,
            args,
            on_section_reviewed=lambda node: completed.append(node["title"]),
        )
    check("CLI 默认只执行基础审校并跳过答案节点", calls == ["basic"])
    check(
        "审校内容包含答案",
        sections[0]["content"]["original"].endswith("**答**：$1$\n\n"),
    )
    check(
        "工具审校关闭时最终版本使用基础审校",
        sections[0]["content"]["tool_review"]
        == sections[0]["content"]["basic_review"],
    )
    check("小节完成后触发实时输出回调", completed == ["习题 1-1"])


def test_cli_engine_device_args() -> None:
    """CLI 能解析统一 OCR engine/device 参数。"""
    from unittest import mock

    from exercise_extractor.main import parse_args

    with mock.patch(
        "sys.argv",
        [
            "exercise_extractor",
            "--input",
            "/tmp/x.pdf",
            "--ocr-device",
            "gpu",
            "--ocr-engine",
            "paddle",
            "--review-concurrency",
            "5",
        ],
    ):
        args = parse_args()
    check(
        "CLI 解析统一 OCR engine/device 与 review 并发数",
        args.ocr_device == "gpu"
        and args.ocr_engine == "paddle"
        and args.review_concurrency == 5
        and not args.review_with_tools,
        str(
            (
                args.ocr_device,
                args.ocr_engine,
            )
        ),
    )
    from exercise_extractor.ocr import _toc_ocr_kwargs

    synced = type("Args", (), {
        "ocr_device": "gpu",
        "ocr_engine": "paddle",
    })()
    check(
        "toc_forge OCR 参数跟随 exercise_extractor",
        _toc_ocr_kwargs(synced) == {"device": "gpu", "engine": "paddle"},
    )
    from exercise_extractor.ocr import _toc_llm_config

    toc_args = type("Args", (), {
        "toc_vllm_name": "test-model",
        "toc_api_base_url": "https://example.test/v1",
        "toc_api_key": "test-key",
    })()
    check(
        "toc_forge vLLM 配置可覆盖",
        _toc_llm_config(toc_args)
        == ("test-model", "https://example.test/v1", "test-key"),
    )


def test_match_section_answers() -> None:
    """流式逐节答案匹配：区间答案展开、题数校验、used 去重。"""
    from exercise_extractor import answers

    secs = [
        {
            "title": "第1章 习题",
            "has_exercise": True,
            "children": [],
            "exercises": [
                {
                    "number": str(i),
                    "content": f"题{i}",
                    "answer": "",
                    "solution_steps": "",
                }
                for i in range(1, 6)
            ],
        }
    ]
    answer_sections = [
        {
            "title": "第1章",
            "answers": [
                {"number": str(i), "content": f"答{i}", "separator": "."}
                for i in (1, 2, 3)
            ]
            + [{"number": "4-5", "content": "答四五", "separator": "."}],
        }
    ]
    targets = answers.collect_exercise_targets(secs)
    used: set[int] = set()
    answers.match_section_answers(answer_sections, targets, 0, used)
    exs = secs[0]["exercises"]
    check(
        "逐节答案匹配+填充",
        all(exs[i]["answer"] == f"答{i + 1}" for i in range(3))
        and exs[3]["answer"] == "答四五"
        and exs[4]["answer"] == "答四五"
        and answer_sections[0].get("count_match") is True
        and used == {0},
        str([e.get("answer") for e in exs]),
    )


def test_oss_image_upload() -> None:
    """OSS 上传：缺配置不启用、本地图片上传并替换 URL、http 引用跳过。"""
    from types import SimpleNamespace
    from unittest import mock

    from exercise_extractor.oss import (
        OssConfig,
        oss_config_from_args,
        upload_images,
    )

    no_cfg = oss_config_from_args(
        SimpleNamespace(
            oss_endpoint=None,
            oss_access_key=None,
            oss_secret_key=None,
            oss_bucket=None,
        )
    )
    check("OSS 缺配置不启用", no_cfg is None)
    cfg = oss_config_from_args(
        SimpleNamespace(
            oss_endpoint="127.0.0.1:9000",
            oss_access_key="a",
            oss_secret_key="s",
            oss_bucket="b",
            oss_region=None,
            oss_prefix="imgs",
            oss_use_ssl=False,
            oss_public_base_url=None,
        )
    )
    check("OSS 配置解析", cfg is not None and cfg.prefix == "imgs")

    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, "page_1", "imgs", "x.jpg")
        os.makedirs(os.path.dirname(img))
        with open(img, "wb") as f:
            f.write(b"fake")
        content = (
            f'<div><img src="{img}" /></div>\n<img src="http://cdn/x.png" />'
        )
        with mock.patch("exercise_extractor.oss._minio_client") as m:
            client = m.return_value
            client.bucket_exists.return_value = True
            new_content, uploaded = upload_images(content, tmp, cfg)
        check(
            "OSS 上传并替换 URL",
            uploaded == 1
            and "http://127.0.0.1:9000/b/imgs/page_1/imgs/x.jpg" in new_content
            and 'src="http://cdn/x.png"' in new_content,
            new_content,
        )


def test_cli() -> None:
    r = run_cli(["--hash", "--input", "/nonexistent.pdf"])
    check(
        "缺失输入 --hash 报错退出",
        r.returncode != 0 and "No such file" in r.stderr + r.stdout,
    )
    r = run_cli(["--input", "/nonexistent.pdf"])
    check("缺失输入退出", r.returncode != 0)
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "dummy"), "w").close()
        r = run_cli(
            ["--input", "/nonexistent.pdf", "--output", tmp, "--doc_type", "textbook"]
        )
        check("非空输出目录保护", r.returncode == 1 and "非空" in r.stdout)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"not a pdf")
        badpdf = f.name
    r = run_cli(["--input", badpdf, "--doc_type", "textbook"], timeout=120)
    check("非 PDF 输入报错退出", r.returncode != 0)
    os.unlink(badpdf)
    with tempfile.TemporaryDirectory() as tmp:
        outfile = os.path.join(tmp, "outfile")
        open(outfile, "w").close()
        r = run_cli(["--input", "/nonexistent.pdf", "--output", outfile])
        check(
            "输出路径为文件报错退出",
            r.returncode == 1 and "不是目录" in r.stdout,
            r.stdout[-200:],
        )
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake minimal")
        hashpdf = f.name
    r = run_cli(["--hash", "--input", hashpdf])
    check(
        "--hash 输出 32 位 hex",
        r.returncode == 0 and len(r.stdout.strip()) == 32,
        r.stdout[-100:],
    )
    os.unlink(hashpdf)
    with tempfile.TemporaryDirectory() as tmp:
        markdown_input = os.path.join(tmp, "习题.md")
        output_dir = os.path.join(tmp, "output")
        with open(markdown_input, "w", encoding="utf-8") as f:
            f.write(
                "## 一、填空。\n\n1. 求结果。\n\n## 答案:\n\n"
                "一、填空题\n\n1. 0\n"
            )
        r = run_cli(
            [
                "--input",
                markdown_input,
                "--output",
                output_dir,
                "--doc_type",
                "exercise_book",
            ]
        )
        with open(os.path.join(output_dir, "report.json"), encoding="utf-8") as f:
            report = json.load(f)
        section_files = [
            name
            for name in os.listdir(output_dir)
            if name.endswith(".json") and name != "report.json"
        ]
        payloads = []
        for name in section_files:
            with open(os.path.join(output_dir, name), encoding="utf-8") as f:
                payloads.append(json.load(f))
        question = next(item for item in payloads if not item.get("is_answers"))
        answer = next(item for item in payloads if item.get("is_answers"))
        check(
            "CLI 支持 Markdown 输入并导出答案 section",
            r.returncode == 0
            and report["total_exercises"] == 1
            and question["exercises"][0]["answer"] == "0"
            and answer.get("is_answers") is True,
            r.stderr[-300:] + r.stdout[-300:],
        )


def test_full_cache_pipeline() -> None:
    """全缓存管线（可选）：需要本机有完整 OCR 缓存的 PDF。

    不绑定任何机器路径：通过环境变量启用
    SMOKE_TEST_FULL_PIPELINE_PDF=<pdf>（可选
    SMOKE_TEST_FULL_PIPELINE_DOC_TYPE，默认 exercise_book）。
    未设置时跳过，保证模块在其他机器上能直接跑通。
    """
    pdf = os.environ.get("SMOKE_TEST_FULL_PIPELINE_PDF")
    if not pdf:
        check(
            "全缓存管线（设置 SMOKE_TEST_FULL_PIPELINE_PDF 启用，当前跳过）",
            True,
        )
        return
    if not os.path.isfile(pdf):
        check("全缓存管线 PDF 不存在", False, pdf)
        return
    doc_type = os.environ.get("SMOKE_TEST_FULL_PIPELINE_DOC_TYPE", "exercise_book")
    outdir = tempfile.mkdtemp(prefix="smoke_out_")
    r = run_cli(
        [
            "--input",
            pdf,
            "--output",
            outdir,
            "--doc_type",
            doc_type,
        ],
        timeout=300,
    )
    check("全缓存管线退出码 0", r.returncode == 0, r.stderr[-300:])
    if r.returncode == 0:
        with open(os.path.join(outdir, "report.json"), encoding="utf-8") as f:
            report = json.load(f)
        check("全缓存报告可解析", report["total_exercises"] > 0)
        check(
            "全缓存 section JSON 结构完整",
            any(
                name.endswith(".json") and name != "report.json"
                for name in os.listdir(outdir)
            ),
        )
