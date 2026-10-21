"""功能区「Zotero 批注」：把当前文档在 Zotero 里的批注导入进来（可撤销）。"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest
from PySide6.QtWidgets import QMessageBox

from pdfreader.gui.main_window import MainWindow
from pdfreader.services.zotero import ZoteroAnnotation, ZoteroError
from tests.unit.gui.test_ai_assistant import _make_doc


class _FakeLibrary:
    """替身：不读真实数据库。"""

    annotations: ClassVar[list[ZoteroAnnotation]] = []
    error: ClassVar[ZoteroError | None] = None
    used_dir: ClassVar[Path | None] = None

    def __init__(self, data_dir: Path) -> None:
        _FakeLibrary.used_dir = data_dir

    def annotations_for(self, _pdf_path: Path) -> list[ZoteroAnnotation]:
        if _FakeLibrary.error is not None:
            raise _FakeLibrary.error
        return list(_FakeLibrary.annotations)


@pytest.fixture
def window(qtbot, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 820)
    w.show()
    qtbot.waitExposed(w)
    yield w
    for tab in w.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()


@pytest.fixture
def messages(window, monkeypatch):  # type: ignore[no-untyped-def]
    """记下状态栏消息（不用等它真的显示出来）。"""
    seen: list[str] = []
    monkeypatch.setattr(window.status_bar, "show_message", seen.append)
    return seen


@pytest.fixture
def fake_zotero(monkeypatch, tmp_path: Path):  # type: ignore[no-untyped-def]
    _FakeLibrary.annotations = []
    _FakeLibrary.error = None
    _FakeLibrary.used_dir = None
    monkeypatch.setattr("pdfreader.gui.main_window.ZoteroLibrary", _FakeLibrary)
    monkeypatch.setattr("pdfreader.gui.main_window.find_data_dir", lambda: tmp_path / "Zotero")
    return _FakeLibrary


def test_action_disabled_without_document(window, fake_zotero, messages) -> None:  # type: ignore[no-untyped-def]
    assert not window.zotero_action.isEnabled()
    window._on_import_zotero()
    assert any("先打开一份 PDF" in m for m in messages)


def test_import_highlight_and_note(window, fake_zotero, messages, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "paper.pdf"))
    assert window.zotero_action.isEnabled()
    fake_zotero.annotations = [
        ZoteroAnnotation(
            "highlight",
            0,
            rects=((72.0, 700.0, 300.0, 715.0),),
            text="attention",
            comment="关键句",
            color="#ff6666",
        ),
        ZoteroAnnotation("note", 1, rects=((80.0, 600.0, 100.0, 620.0),), comment="我的笔记"),
        ZoteroAnnotation("text", 0, rects=((80.0, 500.0, 300.0, 560.0),)),  # 没有文字：跳过
    ]
    window._on_import_zotero()
    annots = window._controller.list_all_annotations()
    assert [a.page_num for a in annots] == [0, 1]
    assert {a.type_name for a in annots} == {"高亮", "便签"}
    assert any(a.content == "关键句" for a in annots)
    assert any(a.content == "我的笔记" for a in annots)
    assert any("已从 Zotero 导入 2 条批注" in m for m in messages)
    assert any("跳过 1 条" in m for m in messages)
    assert window._controller.is_modified
    # 整批一次撤销
    window._on_undo()
    assert window._controller.list_all_annotations() == []


def test_no_annotations_information(window, fake_zotero, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "paper.pdf"))
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: shown.append(str(a[-1])) or QMessageBox.StandardButton.Ok,
    )
    window._on_import_zotero()
    assert shown and "没有这份 PDF 的批注" in shown[0]
    assert not window._controller.is_modified


def test_library_error_reported(window, fake_zotero, monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "paper.pdf"))
    fake_zotero.error = ZoteroError("无法读取 Zotero 库：database is locked")
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *a, **k: warnings.append(str(a[-1])) or QMessageBox.StandardButton.Ok,
    )
    window._on_import_zotero()
    assert warnings == ["无法读取 Zotero 库：database is locked"]
    assert not window._controller.is_modified
    assert fake_zotero.used_dir == tmp_path / "Zotero"


def test_annotations_without_usable_geometry(
    window, fake_zotero, monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "paper.pdf"))
    fake_zotero.annotations = [
        ZoteroAnnotation("text", 0, rects=((5.0, 5.0, 90.0, 40.0),)),  # 文本框没有文字
    ]
    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *a, **k: shown.append(str(a[-1])) or QMessageBox.StandardButton.Ok,
    )
    window._on_import_zotero()
    assert shown and "都没有能写进这份 PDF 的" in shown[0]
    assert not window._controller.is_modified


def test_configured_folder_is_used(window, fake_zotero, messages, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "paper.pdf"))
    folder = tmp_path / "CustomZotero"
    folder.mkdir()
    (folder / "zotero.sqlite").write_bytes(b"")
    window._settings.zotero_data_dir = str(folder)
    window._on_import_zotero()
    assert fake_zotero.used_dir == folder
