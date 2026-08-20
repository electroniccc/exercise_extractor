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
        open(os.path.join(tmp, "page_1", "page.md"), "w").close()
        os.makedirs(os.path.join(tmp, "page_2"))
        open(os.path.join(tmp, "page_2", "FAILED.txt"), "w").close()
        pages = collect_pages_to_ocr(
            [{"has_exercise": True, "exercise_page_range": [1, 3]}], tmp
        )
        check("FAILED 页仍待重试", pages == [2], f"got {pages}")

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

    from exercise_extractor.api import extract_exercise

    params = inspect.signature(extract_exercise).parameters
    check(
        "API 暴露 ocr/toc engine/device 参数",
        all(
            k in params
            for k in (
                "ocr_device",
                "ocr_engine",
                "toc_device",
                "toc_engine",
            )
        ),
    )

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
                        }
                    ],
                }
            ],
        }
    ]
    with patch(
        "exercise_extractor.api.iter_sections",
        return_value=iter([internal_nodes[0]["children"][0]]),
    ):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake minimal")
            public_pdf = f.name
        try:
            sections = list(extract_exercise(public_pdf, model_dir="/tmp/models"))
        finally:
            os.unlink(public_pdf)
    check(
        "extract_exercise 返回扁平公共 section",
        len(sections) == 1
        and sections[0]["path"] == "第一章/习题一"
        and "children" not in sections[0],
    )


def test_llm_review_contract() -> None:
    """LLM 审校接口只改文本并按题目追加状态，工具轮次可控。"""
    from exercise_extractor.review import (
        DEFAULT_REVIEW_BASE_URL,
        DEFAULT_REVIEW_MODEL,
        review_basic,
        review_markdown_basic,
        review_markdown_with_tools,
    )

    check("审校默认模型", DEFAULT_REVIEW_MODEL == "qwen3.7-max-2026-05-17")
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
        sections[0]["content"]["original"].endswith("答：$1$\n\n"),
    )
    check(
        "工具审校关闭时最终版本使用基础审校",
        sections[0]["content"]["tool_review"]
        == sections[0]["content"]["basic_review"],
    )
    check("小节完成后触发实时输出回调", completed == ["习题 1-1"])


def test_cli_engine_device_args() -> None:
    """CLI 能解析 ocr/toc 的 engine/device 参数。"""
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
            "--toc-device",
            "cpu",
            "--toc-engine",
            "onnxruntime",
            "--review-concurrency",
            "5",
        ],
    ):
        args = parse_args()
    check(
        "CLI 解析 ocr/toc engine/device 与 review 并发数",
        args.ocr_device == "gpu"
        and args.ocr_engine == "paddle"
        and args.toc_device == "cpu"
        and args.toc_engine == "onnxruntime"
        and args.review_concurrency == 5
        and not args.review_with_tools,
        str(
            (
                args.ocr_device,
                args.ocr_engine,
                args.toc_device,
                args.toc_engine,
            )
        ),
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
        "--hash 输出 16 位 hex",
        r.returncode == 0 and len(r.stdout.strip()) == 16,
        r.stdout[-100:],
    )
    os.unlink(hashpdf)


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
