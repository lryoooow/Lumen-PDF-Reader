"""关于对话框里的诊断导出入口。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton

from pdfreader.gui.dialogs import AboutDialog
from pdfreader.gui.main_window import MainWindow


def test_about_dialog_emits_diagnostics_request(qtbot) -> None:  # type: ignore[no-untyped-def]
    dialog = AboutDialog()
    qtbot.addWidget(dialog)
    buttons = [b for b in dialog.findChildren(QPushButton) if b.text().startswith("导出诊断")]
    assert len(buttons) == 1
    with qtbot.waitSignal(dialog.diagnosticsRequested, timeout=2000):
        qtbot.mouseClick(buttons[0], Qt.MouseButton.LeftButton)


def test_export_diagnostics_writes_zip(qtbot, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from pdfreader.services import diagnostics

    target = tmp_path / "diag.zip"

    def fake_collect(destination: Path) -> Path:
        destination.write_bytes(b"zip")
        return destination

    monkeypatch.setattr(diagnostics, "collect_diagnostics", fake_collect)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "ZIP 压缩包 (*.zip)")
    )
    shown: list[str] = []

    def fake_info(*args, **kwargs):  # type: ignore[no-untyped-def]
        shown.append(str(args[2]))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", fake_info)
    window = MainWindow()
    qtbot.addWidget(window)
    window._on_export_diagnostics()
    assert target.is_file()
    assert shown and str(target) in shown[0]
