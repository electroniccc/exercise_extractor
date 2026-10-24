"""测试入口：扫描 test/test_scenarios 依次跑场景，再跑单元测试。"""

import os
import sys

from . import common, unit
from .scenarios import SCENARIO_TESTS, SECONDARY_SCENARIO_FILES


def main() -> None:
    sys.path.insert(0, common.ROOT)
    print("== 场景化测试（test/test_scenarios/*.txt）==")
    for fname in sorted(os.listdir(common.SCENARIO_DIR)):
        if not fname.endswith(".txt") or fname in SECONDARY_SCENARIO_FILES:
            continue
        fn = SCENARIO_TESTS.get(fname)
        if fn is None:
            common.check(
                f"场景未注册: {fname}",
                False,
                "test/test_scenarios 中的 txt 未在 SCENARIO_TESTS 注册",
            )
            continue
        print(f"\n-- 场景: {fname} --")
        fn()

    print("\n== 非场景单元测试 ==")
    unit.test_unit_boundaries()
    unit.test_toc_and_boundaries()
    unit.test_solution_format_normalization()
    unit.test_review_does_not_invent_answer_for_proof()
    unit.test_single_review_restores_truncated_numbered_solution()
    unit.test_solution_marker_before_parenthesized_subquestion()
    unit.test_answers_match()
    unit.test_html_tables()
    unit.test_exercise_book_sections()
    unit.test_exampaper_build_node()
    unit.test_api_contract()
    unit.test_exercise_bbox()
    unit.test_ocr_cache_concurrency()
    unit.test_declared_blank_pdf_page()
    unit.test_ocr_page_batches()
    unit.test_llm_review_contract()
    unit.test_review_removes_supplemented_answers()
    unit.test_review_reassigns_existing_answers()
    unit.test_review_prefers_current_draft_over_ocr_reference()
    unit.test_reparse_preserves_pages_by_number()
    unit.test_single_review_uses_reference_without_replacing_current_draft()
    unit.test_single_review_restores_missing_solution_from_reference()
    unit.test_review_uses_raw_ocr_markdown_for_parenthesized_answers()
    unit.test_review_recovers_when_llm_keeps_parenthesized_question_numbers()
    unit.test_raw_ocr_review_matches_then_formats_as_structured_exercises()
    unit.test_section_review_recovers_explicit_numbered_boundary()
    unit.test_single_question_raw_ocr_solution_survives_review()
    unit.test_review_wraps_unmarked_latex_answer_in_dollars()
    unit.test_display_math_is_rendered_as_a_standalone_block()
    unit.test_exercise_markdown_renderer_separates_display_math_from_labels()
    unit.test_review_keeps_partial_tool_result()
    unit.test_cli_review_skips_answer_nodes()
    unit.test_cli_engine_device_args()
    unit.test_match_section_answers()
    unit.test_oss_image_upload()
    unit.test_cli()
    unit.test_full_cache_pipeline()

    print(f"\n===== 结果: {common.PASS} 通过, {common.FAIL} 失败 =====")
    sys.exit(1 if common.FAIL else 0)
