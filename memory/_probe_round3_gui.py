"""第三轮复查探针：GUI 层（离屏 Qt）。

用法：PYTHONIOENCODING=utf-8 uv run python memory/_probe_round3_gui.py
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf  # noqa: E402  探针例外：只用于造样例文件
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pdfreader.engines.types import Rect  # noqa: E402
from pdfreader.gui.main_window import MainWindow  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="probe_r3g_"))
app = QApplication.instance() or QApplication([])


def make_pdf(path: Path, pages: int) -> Path:
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 72), f"page {i + 1} hello world")
    d.save(str(path))
    d.close()
    return path


def pump(n: int = 5) -> None:
    for _ in range(n):
        app.processEvents()


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# 不弹真实对话框
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)  # type: ignore[method-assign]
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)  # type: ignore[method-assign]

w = MainWindow()
w.resize(1000, 800)
w.show()
pump()

# ---------------------------------------------------------------- A 编辑后当前页丢失
section("A 旋转第 4 页后，再点「旋转/删除」作用在哪一页")
src = make_pdf(TMP / "a.pdf", 8)
w.open_path(str(src))
pump()
w.pdf_view.jump_to_page(3)
pump()
print("跳到第 4 页后 current_page_num =", w.pdf_view.current_page_num)
w._on_rotate_page()
pump()
print("旋转后 current_page_num =", w.pdf_view.current_page_num,
      "→ _current_page() =", w._current_page())
doc = w._controller.document
assert doc is not None
rots = [doc._handle.native[i].rotation for i in range(doc.page_count)]
w._on_rotate_page()
pump()
rots2 = [doc._handle.native[i].rotation for i in range(doc.page_count)]
print("第一次旋转后各页角度 =", rots)
print("第二次旋转后各页角度 =", rots2, "（期望只有第 4 页变成 180）")

# ---------------------------------------------------------------- B 导出默认路径落在快照目录
section("B 有未保存改动时导出：源与默认输出路径")
from pdfreader.gui.dialogs import export_dialog  # noqa: E402

captured: dict[str, object] = {}


class _SpyDialog(export_dialog.ExportDialog):
    def exec(self) -> int:  # type: ignore[override]
        captured["source"] = self._source
        captured["default_out"] = self._path_edit.text()
        return 0


export_dialog_orig = export_dialog.ExportDialog
import pdfreader.gui.dialogs as dialogs_pkg  # noqa: E402

dialogs_pkg.ExportDialog = _SpyDialog  # type: ignore[misc]
w._controller.add_highlight(0, Rect(72, 60, 200, 80))
w._on_export()
dialogs_pkg.ExportDialog = export_dialog_orig  # type: ignore[misc]
print("导出源 =", captured.get("source"))
print("默认输出 =", captured.get("default_out"))
print("快照目录 =", w._snapshot_dir, "（退出时 _cleanup_snapshots 会 rmtree 整个目录）")
print("默认输出位于快照目录内 =",
      str(captured.get("default_out", "")).startswith(str(w._snapshot_dir)))

# ---------------------------------------------------------------- C 切换文档时旧搜索线程
section("C 切换到另一文档后，旧文档的搜索结果是否会显示到新文档面板")
src2 = make_pdf(TMP / "c2.pdf", 2)
w._controller.document.mark_saved()  # 避免保存询问
w._on_search_requested("hello")
old_worker = w._search_worker
w.open_path(str(src2))  # 切换文档（静默关闭旧文档，不发 documentClosed）
print("切换后 _search_worker 是否仍是旧线程 =", w._search_worker is old_worker,
      "；_active_query =", repr(w._active_query))
if old_worker is not None:
    old_worker.wait(3000)
pump(20)
print("新文档的搜索面板结果数 =", w.search_panel._result_list.count(),
      "（新文档只有 2 页，旧文档 8 页；>0 即为串台）")

# ---------------------------------------------------------------- D 撤销后当前页
section("D 撤销后 current_page_num 是否有效")
w._controller.document.mark_saved()
w.open_path(str(src))
pump()
w.pdf_view.jump_to_page(5)
pump()
w._controller.add_highlight(5, Rect(72, 60, 200, 80))
w._on_undo()
pump()
print("撤销后 current_page_num =", w.pdf_view.current_page_num)

w._controller.document.mark_saved()
w.close()
pump()
shutil.rmtree(TMP, ignore_errors=True)
