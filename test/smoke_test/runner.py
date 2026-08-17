"""测试入口：扫描 test_scenarios 依次跑场景，再跑单元测试。"""

import os
import sys

from . import common, unit
from .scenarios import SCENARIO_TESTS, SECONDARY_SCENARIO_FILES


def main() -> None:
    sys.path.insert(0, common.ROOT)
    print("== 场景化测试（test_scenarios/*.txt）==")
    for fname in sorted(os.listdir(common.SCENARIO_DIR)):
        if not fname.endswith(".txt") or fname in SECONDARY_SCENARIO_FILES:
            continue
        fn = SCENARIO_TESTS.get(fname)
        if fn is None:
            common.check(
                f"场景未注册: {fname}",
                False,
                "test_scenarios 中的 txt 未在 SCENARIO_TESTS 注册",
            )
            continue
        print(f"\n-- 场景: {fname} --")
        fn()

    print("\n== 非场景单元测试 ==")
    unit.test_unit_boundaries()
    unit.test_toc_and_boundaries()
    unit.test_answers_match()
    unit.test_html_tables()
    unit.test_exercise_book_sections()
    unit.test_exampaper_build_node()
    unit.test_api_contract()
    unit.test_cli_engine_device_args()
    unit.test_match_section_answers()
    unit.test_cli()
    unit.test_full_cache_pipeline()

    print(f"\n===== 结果: {common.PASS} 通过, {common.FAIL} 失败 =====")
    sys.exit(1 if common.FAIL else 0)
