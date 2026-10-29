"""隔离原生模块导入，防止依赖初始化崩溃直接终止整个 pytest 进程。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("qt_first", [False, True], ids=["engine-first", "qt-first"])
def test_native_imports_with_strict_warnings(qt_first: bool) -> None:
    """Qt 两种加载顺序均须能在严格警告模式下载入、创建并渲染 PDF。"""
    imports = ["import pymupdf", "from PySide6 import QtWidgets"]
    if qt_first:
        imports.reverse()
    script = "\n".join(
        [
            "import warnings",
            *imports,
            "with pymupdf.open() as document:",
            "    page = document.new_page()",
            "    page.insert_text((72, 72), 'Lumen native import regression')",
            "    assert page.get_pixmap().width > 0",
            "try:",
            "    warnings.warn('Lumen unexpected warning', DeprecationWarning)",
            "except DeprecationWarning:",
            "    pass",
            "else:",
            "    raise AssertionError('Strict warning handling was disabled')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-W", "error", "-c", script],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"Native import process exited {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
