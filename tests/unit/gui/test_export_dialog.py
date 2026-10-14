"""ExportDialog 后台转换接线测试（R2-P1-5）。

不真的跑转换：把 `ConvertService.convert` 换成可控替身，验证
「点击开始转换 → 在后台线程跑 → 运行中禁用按钮并显示进度 → 完成/失败提示」这条线。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from pdfreader.gui.dialogs import ExportDialog
from pdfreader.services.converter import ConvertError, ConvertResult, ConvertService

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SIMPLE = SAMPLES / "simple_text.pdf"


@pytest.fixture
def dialog(qtbot) -> ExportDialog:  # type: ignore[no-untyped-def]
    dlg = ExportDialog(SIMPLE)
    qtbot.addWidget(dlg)
    return dlg


def test_convert_runs_in_background_thread(dialog, qtbot, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """转换不能在 GUI 线程执行：调用期间界面应保持可用（按钮禁用 + 进度条可见）。"""
    seen: list[tuple] = []

    def slow_convert(self, source, output, fmt, dpi=150, **_kw):  # type: ignore[no-untyped-def]
        seen.append((source, output, fmt))
        time.sleep(0.2)  # 模拟耗时转换
        return ConvertResult(output, "纯文本", 1)

    monkeypatch.setattr(ConvertService, "convert", slow_convert)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog._path_edit.setText(str(tmp_path / "out"))
    dialog._format_combo.setCurrentIndex(dialog._format_combo.findData("txt"))

    dialog._on_convert()

    assert dialog._worker is not None
    # 对话框本身没 show()，用"自身 hidden 标志"判断进度条是否被显式打开
    assert dialog._progress.isHidden() is False
    assert dialog._convert_btn.isEnabled() is False
    diag = dialog._worker  # 保引用，等它结束
    with qtbot.waitSignal(diag.convertFinished, timeout=10000):
        pass
    qtbot.waitUntil(lambda: not diag.isRunning(), timeout=5000)
    assert seen and seen[0][2] == "txt"
    assert dialog.result() == ExportDialog.DialogCode.Accepted


def test_convert_failure_keeps_dialog_open(dialog, qtbot, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """失败走 convertFailed：提示错误、恢复按钮、不关闭对话框。"""
    errors: list[str] = []

    def boom(self, source, output, fmt, dpi=150, **_kw):  # type: ignore[no-untyped-def]
        raise ConvertError("转换炸了")

    monkeypatch.setattr(ConvertService, "convert", boom)
    monkeypatch.setattr(QMessageBox, "critical", lambda parent, title, text: errors.append(text))
    dialog._path_edit.setText(str(tmp_path / "out"))

    dialog._on_convert()
    worker = dialog._worker
    assert worker is not None
    with qtbot.waitSignal(worker.convertFailed, timeout=10000):
        pass
    qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)

    assert errors == ["转换炸了"]
    assert dialog._progress.isHidden() is True
    assert dialog._convert_btn.isEnabled() is True
    assert dialog.result() != ExportDialog.DialogCode.Accepted


def test_convert_requires_output_path(dialog, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: warnings.append(text))
    dialog._path_edit.setText("   ")

    dialog._on_convert()

    assert warnings == ["请指定输出路径"]
    assert dialog._worker is None


def test_close_ignored_while_converting(dialog, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """转换未结束时不允许关闭（QThread 运行中被销毁会崩）。"""
    from PySide6.QtGui import QCloseEvent

    def slow_convert(self, source, output, fmt, dpi=150, **_kw):  # type: ignore[no-untyped-def]
        time.sleep(0.3)
        return ConvertResult(output, "纯文本", 1)

    monkeypatch.setattr(ConvertService, "convert", slow_convert)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog._path_edit.setText(str(tmp_path / "out"))
    dialog._on_convert()

    event = QCloseEvent()
    dialog.closeEvent(event)
    assert event.isAccepted() is False
    assert "转换进行中" in dialog._hint.text()
    dialog._worker.wait(10000)


def test_default_output_uses_display_path_not_snapshot(qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R3-02：读快照，但标题与默认输出必须是真实文档的位置（快照目录退出时会被清理）。"""
    snapshot = tmp_path / "snapshots" / "snapshot_abc.pdf"
    real = tmp_path / "papers" / "paper.pdf"
    dlg = ExportDialog(snapshot, display_path=real, page_count=3)
    qtbot.addWidget(dlg)
    assert dlg._path_edit.text() == str(real.parent / "paper")
    assert dlg._source == snapshot


def test_existing_target_requires_confirmation(qtbot, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R3-02：目标已存在（含多页图片的 stem_N）时必须先确认，拒绝则不转换。"""
    asked: list[str] = []
    (tmp_path / "doc_2.png").write_bytes(b"old")
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    dlg = ExportDialog(SIMPLE, page_count=3)
    qtbot.addWidget(dlg)
    dlg._path_edit.setText(str(tmp_path / "doc"))
    dlg._format_combo.setCurrentIndex(dlg._format_combo.findData("png"))
    dlg._on_convert()
    assert asked and "doc_2.png" in asked[0]
    assert dlg._worker is None
    assert (tmp_path / "doc_2.png").read_bytes() == b"old"


def test_reject_ignored_while_converting(dialog, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R3-10：转换中按 Esc / 取消不能关掉对话框（reject 不经过 closeEvent）。"""

    def slow_convert(self, source, output, fmt, dpi=150, **_kw):  # type: ignore[no-untyped-def]
        time.sleep(0.3)
        return ConvertResult(output, "纯文本", 1)

    monkeypatch.setattr(ConvertService, "convert", slow_convert)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog._path_edit.setText(str(tmp_path / "out"))
    closed: list[int] = []
    dialog.finished.connect(closed.append)
    dialog._on_convert()
    dialog.reject()
    assert closed == []  # 没有真的关闭
    assert "转换进行中" in dialog._hint.text()
    dialog._worker.wait(10000)


def test_result_message_lists_real_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.gui.dialogs.export_dialog import _describe_result

    files = (tmp_path / "a_1.png", tmp_path / "a_2.png")
    msg = _describe_result(ConvertResult(tmp_path / "a.png", "PNG 图片", 2, files))
    assert "a_1.png" in msg and "a_2.png" in msg and "2 个文件" in msg
    empty = _describe_result(ConvertResult(tmp_path / "t.xlsx", "Excel 表格", 0, ()))
    assert "未在文档中检测到表格" in empty


def test_password_passed_to_worker(qtbot, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """R3-03：加密文档导出时密码要一路传到转换服务。"""
    got: list[object] = []

    def fake_convert(self, source, output, fmt, dpi=150, *, password=None):  # type: ignore[no-untyped-def]
        got.append(password)
        return ConvertResult(output, "纯文本", 1, (output,))

    monkeypatch.setattr(ConvertService, "convert", fake_convert)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg = ExportDialog(SIMPLE, password="secret")
    qtbot.addWidget(dlg)
    dlg._path_edit.setText(str(tmp_path / "out"))
    dlg._format_combo.setCurrentIndex(dlg._format_combo.findData("txt"))
    dlg._on_convert()
    worker = dlg._worker
    assert worker is not None
    worker.wait(10000)
    qtbot.waitUntil(lambda: bool(got), timeout=5000)
    assert got == ["secret"]
