"""OCR 页面缓存的并发控制与原子发布。"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import shutil
import tempfile
from typing import Iterator

from filelock import FileLock


READY_FILENAME = "READY"


def page_cache_dir(markdown_path: str, page_num: int) -> str:
    return os.path.join(markdown_path, f"page_{page_num}")


def page_cache_is_ready(markdown_path: str, page_num: int) -> bool:
    """页面是否为完整发布的新版缓存。缺少 READY 的旧缓存一律无效。"""
    page_dir = page_cache_dir(markdown_path, page_num)
    required = (
        os.path.join(page_dir, "page.md"),
        os.path.join(page_dir, "page.json"),
        os.path.join(page_dir, READY_FILENAME),
    )
    try:
        return all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in required)
    except OSError:
        return False


@contextmanager
def ocr_cache_lock(markdown_path: str) -> Iterator[None]:
    """按 PDF hash 目录串行化 OCR 缓存生产，兼容线程和本机多进程。"""
    hash_dir = os.path.dirname(os.path.abspath(markdown_path))
    os.makedirs(hash_dir, exist_ok=True)
    with FileLock(os.path.join(hash_dir, ".ocr.lock")):
        yield


def create_page_staging_dir(markdown_path: str, page_num: int) -> str:
    """在最终缓存同一文件系统内创建页面临时目录，保证 rename 原子。"""
    os.makedirs(markdown_path, exist_ok=True)
    return tempfile.mkdtemp(prefix=f".page_{page_num}.", dir=markdown_path)


def _remove_path(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def discard_page_staging(staging_dir: str) -> None:
    if os.path.lexists(staging_dir):
        _remove_path(staging_dir)


def publish_page_cache(
    staging_dir: str,
    markdown_path: str,
    page_num: int,
    *,
    replace_ready: bool = False,
) -> bool:
    """校验临时页面并原子发布；READY 是发布前最后写入的文件。"""
    markdown_file = os.path.join(staging_dir, "page.md")
    json_file = os.path.join(staging_dir, "page.json")
    if not os.path.isfile(markdown_file) or os.path.getsize(markdown_file) == 0:
        raise ValueError(f"第 {page_num} 页 OCR Markdown 为空")
    if not os.path.isfile(json_file) or os.path.getsize(json_file) == 0:
        raise ValueError(f"第 {page_num} 页 OCR JSON 为空")
    with open(json_file, encoding="utf-8") as stream:
        json.load(stream)

    ready_file = os.path.join(staging_dir, READY_FILENAME)
    with open(ready_file, "x", encoding="utf-8") as stream:
        stream.write("ready\n")

    final_dir = page_cache_dir(markdown_path, page_num)
    if page_cache_is_ready(markdown_path, page_num) and not replace_ready:
        discard_page_staging(staging_dir)
        return False
    if page_cache_is_ready(markdown_path, page_num) and replace_ready:
        backup_dir = tempfile.mkdtemp(prefix=f".page_{page_num}.backup.", dir=markdown_path)
        os.rmdir(backup_dir)
        os.replace(final_dir, backup_dir)
        try:
            os.replace(staging_dir, final_dir)
        except BaseException:
            os.replace(backup_dir, final_dir)
            raise
        discard_page_staging(backup_dir)
        return True
    # 这里只有缺少 READY 的旧缓存或上次崩溃留下的半成品；调用者持有
    # 文档级锁，因此清理后到 os.replace 之间不会被内部读者观察到。
    if os.path.lexists(final_dir):
        _remove_path(final_dir)
    os.replace(staging_dir, final_dir)
    return True


def page_failure_path(markdown_path: str, page_num: int) -> str:
    return os.path.join(markdown_path, ".failures", f"page_{page_num}.txt")


def record_page_failure(markdown_path: str, page_num: int, error: Exception) -> None:
    """在页面目录之外记录失败，避免半成品被误认为已发布缓存。"""
    failure_path = page_failure_path(markdown_path, page_num)
    os.makedirs(os.path.dirname(failure_path), exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".page_{page_num}.", dir=os.path.dirname(failure_path), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(error))
        os.replace(temp_path, failure_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def clear_page_failure(markdown_path: str, page_num: int) -> None:
    try:
        os.unlink(page_failure_path(markdown_path, page_num))
    except FileNotFoundError:
        pass
