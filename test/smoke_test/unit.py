"""非场景单元测试（单行/字典结构的小断言，不抽成 txt）。"""

import json
import os
import tempfile

from .common import check, run_cli


def test_unit_boundaries() -> None:
    from exercise_extractor.parse_md_exercises import split_exercises

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
        with open(os.path.join(outdir, "exercises.json"), encoding="utf-8") as f:
            data = json.load(f)
        check(
            "全缓存 exercises.json 结构完整",
            isinstance(data, list) and len(data) > 0,
        )
