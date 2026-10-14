"""验证：后台线程持有旧快照时仍能生成新快照（原 P0-2 的回归脚本）。

修复前实测：`_worker_source` 用固定路径 `%TEMP%/pdfreader_snapshot_<pid>.pdf`，
搜索/OCR 线程已打开该文件时第二次 `save_copy` 抛
`FzErrorSystem code=2: cannot remove file ...: Permission denied`，
`if save_copy(tmp): return tmp` 失败后 `return doc.path` → 后台线程静默读到**磁盘旧内容**。

修复后：每次生成唯一文件名快照；写快照失败时返回 None（调用方取消操作），
绝不退回过时文件；退出时清理快照目录。

运行：uv run python memory/repro_snapshot_lock.py   （全部 PASS 时退出码 0）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pdfreader.engines import PyMuPdfEngine  # noqa: E402
from pdfreader.engines.types import Rect  # noqa: E402
from pdfreader.gui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])
window = MainWindow()
SAMPLE = ROOT / "tests/fixtures/samples/simple_text.pdf"
engine = PyMuPdfEngine()
failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        failures.append(msg)


window._controller.open_file(SAMPLE)
window._controller.add_highlight(0, Rect(40, 40, 200, 60))  # 变脏 → 走快照

first = window._worker_source()
check(first is not None and first is not None and first.exists(), f"第一份快照：{first}")

holder = engine.open(first)  # 模拟搜索/OCR 线程仍打开旧快照
print("  （模拟后台线程仍持有第一份快照）")

window._controller.add_highlight(0, Rect(40, 100, 300, 130))
second = window._worker_source()
check(second is not None and second != first, f"第二份快照路径不同：{second}")
check(second is not None and second.exists(), "第二份快照写入成功（不再 Permission denied）")
if second is not None:
    check(
        len(engine.list_annotations(engine.open(second), 0)) == 2,
        "第二份快照内容是含最新改动的内存文档",
    )
check(first.exists(), "被后台线程占用的旧快照保留（Windows 上删不掉，忽略即可）")

holder.close()
window._worker_source()  # 再取一次：此时旧快照已释放，应被清理
check(
    not first.exists() and second is not None and not second.exists(),
    "释放后旧快照已被清理",
)

# 快照失败时不得退回磁盘旧文件
window._controller.save_copy = lambda _path: False  # type: ignore[assignment,method-assign]
check(window._worker_source() is None, "写快照失败 → 返回 None（不退回磁盘旧文件）")
window._controller.close_document()  # 否则 closeEvent 会弹「保存更改」询问框

window.close()
check(not window._snapshot_dir.exists(), "退出后快照目录已清理")
print("快照目录 =", window._snapshot_dir)
if failures:
    print(f"结果：{len(failures)} 项未通过")
    sys.exit(1)
print("结果：全部通过")
