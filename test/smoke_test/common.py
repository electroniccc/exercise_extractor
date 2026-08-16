"""冒烟测试公共设施：断言计数、CLI 子进程、场景文件加载。"""

import os
import subprocess
import sys

# test/smoke_test/common.py → 仓库根目录
ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SCENARIO_DIR = os.path.join(ROOT, "test_scenarios")
# 用当前解释器跑 CLI 子进程，避免绑定本机 .venv 路径
PY = sys.executable

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def run_cli(args: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    """以仓库根 main.py（兼容 shim）跑 CLI 子进程。"""
    return subprocess.run(
        [PY, os.path.join(ROOT, "main.py"), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=ROOT,
    )


def load_scenario(name: str) -> str:
    """读取 test_scenarios 下的场景文本。"""
    with open(os.path.join(SCENARIO_DIR, name), encoding="utf-8") as f:
        return f.read()


def _exampaper_node(title: str, content: str) -> dict:
    return {
        "title": title,
        "exampaper": True,
        "has_exercise": True,
        "children": [],
        "exercise_content": content,
    }
