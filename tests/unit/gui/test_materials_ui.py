"""侧栏「素材」页与主窗口接线：显示 / 搜索 / 跳回原文 / 编辑 / 删除 / 导出。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.panels import SidePanel
from pdfreader.services.materials import Material
from tests.unit.gui.test_ai_assistant import _make_doc


def _m(mid: int, kind: str, text: str, note: str = "", **kw) -> Material:  # type: ignore[no-untyped-def]
    return Material(mid, kind, text, note, **kw)


class TestSidebarPage:
    def test_cards_and_search(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        side = SidePanel()
        qtbot.addWidget(side)
        side.show()
        side.tabs.setCurrentIndex(SidePanel.TAB_MATERIALS)
        assert (
            side.material_empty.isVisibleTo(side) and "单词本还是空的" in side.material_empty.text()
        )
        assert not side.material_export_btn.isEnabled()
        side.set_materials(
            [
                _m(2, "word", "gradient", "n. 梯度", extra={"phonetic": "ˈɡreɪdiənt"}),
                _m(1, "word", "attention", "n. 注意", doc_title="Paper", page=2),
            ]
        )
        assert side.material_list.count() == 2 and side.material_export_btn.isEnabled()
        assert "gradient  /ˈɡreɪdiənt/" in side.material_list.item(0).text()
        assert "Paper · 第 3 页" in side.material_list.item(1).text()
        side.material_search.setText("注意")
        assert [m.text for m in side.visible_materials()] == ["attention"]
        side.material_search.setText("zzz")
        assert side.material_no_match.isVisibleTo(side)

    def test_kind_change_and_click(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        side = SidePanel()
        qtbot.addWidget(side)
        with qtbot.waitSignal(side.materialKindChanged) as blocker:
            side.set_material_kind("term")
        assert blocker.args == ["term"] and side.material_kind_key == "term"
        side.set_materials([_m(5, "term", "attention", "注意力")])
        assert "attention → 注意力" in side.material_list.item(0).text()
        with qtbot.waitSignal(side.materialActivated) as clicked:
            side._on_material_clicked(side.material_list.item(0))
        assert clicked.args[0].id == 5
        with qtbot.waitSignal(side.materialsExportRequested) as exported:
            side.material_export_btn.click()
        assert exported.args == ["term"]

    def test_clear_keeps_materials(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        """换文档清空侧栏时，素材（跨文档）不受影响。"""
        side = SidePanel()
        qtbot.addWidget(side)
        side.set_materials([_m(1, "word", "gradient")])
        side.clear()
        assert side.material_list.count() == 1


@pytest.fixture
def window(qtbot, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 820)
    w.show()
    qtbot.waitExposed(w)
    yield w
    for tab in w.tabs:
        if tab.controller.document is not None:
            tab.controller.document.mark_saved()
    w.close()


class TestMainWindowMaterials:
    def test_lazy_refresh_and_actions(self, window, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        doc = _make_doc(tmp_path / "paper.pdf")
        store = window._materials
        store.add("corpus", "Attention is all you need.", doc_path=str(doc), doc_title="P", page=2)
        window.open_path(_make_doc(tmp_path / "other.pdf"))
        window.sidebar.tabs.setCurrentIndex(SidePanel.TAB_MATERIALS)
        window.sidebar.set_material_kind("corpus")
        assert window.sidebar.material_list.count() == 1
        item = window.sidebar.material_list.item(0)
        # 单击：打开原文档并翻到那一页
        window.sidebar._on_material_clicked(item)
        assert window.current_tab.path is not None and window.current_tab.path.name == "paper.pdf"
        assert window.pdf_view.current_page_num == 2

        m = store.items("corpus")[0]
        monkeypatch.setattr(
            QInputDialog, "getMultiLineText", lambda *a, **k: ("注意力就是一切", True)
        )
        window.sidebar.materialEditRequested.emit(m)
        assert store.get(m.id).note == "注意力就是一切"  # type: ignore[union-attr]
        window.sidebar.materialDeleteRequested.emit(m)
        assert store.count("corpus") == 0 and window.sidebar.material_list.count() == 0

    def test_missing_source(self, window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "open.pdf"))  # 有文档时才显示状态栏
        m = window._materials.add("word", "gradient", doc_path=str(tmp_path / "gone.pdf"), page=0)
        window._on_material_activated(m)
        assert "找不到原文档" in window.status_bar.message()

    def test_export_markdown_and_csv(self, window, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        window._materials.add("word", "gradient", "n. 梯度")
        md = tmp_path / "words.md"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(md), "Markdown (*.md)")
        )
        window._on_materials_export("word")
        assert "- **gradient**：n. 梯度" in md.read_text(encoding="utf-8")
        csv_path = tmp_path / "words.csv"
        monkeypatch.setattr(
            QFileDialog, "getSaveFileName", lambda *a, **k: (str(csv_path), "CSV 表格 (*.csv)")
        )
        window._on_materials_export("word")
        raw = csv_path.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")  # BOM：Excel 直接打开不乱码
        assert "gradient" in raw.decode("utf-8-sig")

    def test_word_saved_from_popup_refreshes_sidebar(self, window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "a.pdf"))
        window.sidebar.tabs.setCurrentIndex(SidePanel.TAB_MATERIALS)
        tr = window.current_tab.translator
        tr._entry = tr._dictionary.lookup("gradient")
        tr._page = 0
        tr._on_word_save(True)
        assert window.sidebar.material_list.count() == 1
        saved = window._materials.find("word", "gradient")
        assert saved is not None and saved.doc_path.endswith("a.pdf")


def _select_first_line(window) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QPointF

    words = window._controller.get_words(0)
    first = [w for w in words if (w.block, w.line) == (words[0].block, words[0].line)]
    a, b = first[0].rect, first[-1].rect
    window.pdf_view._select_range(
        0,
        QPointF((a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2),
        QPointF((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2),
    )


class TestAddFromSelection:
    def test_corpus_and_quote(self, window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "paper.pdf"))
        _select_first_line(window)
        window.pdf_view.materialRequested.emit("corpus")
        window.pdf_view.materialRequested.emit("quote")
        corpus = window._materials.items("corpus")
        quote = window._materials.items("quote")
        assert corpus[0].text == "Section 1 attention mechanism" and corpus[0].page == 0
        assert corpus[0].doc_path.endswith("paper.pdf")
        assert quote[0].note == "Previous studies have shown that section 1 attention mechanism"

    def test_term_asks_for_translation(self, window, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "paper.pdf"))
        _select_first_line(window)
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("第一节", True))
        window.pdf_view.materialRequested.emit("term")
        term = window._materials.find("term", "Section 1 attention mechanism")
        assert term is not None and term.note == "第一节"
        # 取消 / 空译名：不保存
        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", True))
        window._materials.remove(term.id)
        window.pdf_view.materialRequested.emit("term")
        assert window._materials.count("term") == 0

    def test_no_selection(self, window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "paper.pdf"))
        window.pdf_view.materialRequested.emit("corpus")
        assert window._materials.count("corpus") == 0
        assert "选中文字" in window.status_bar.message()

    def test_glossary_toggle(self, window) -> None:  # type: ignore[no-untyped-def]
        window._materials.add("term", "attention", "注意力")
        assert window._glossary_for("Attention helps.") == [("attention", "注意力")]
        window.glossary_action.trigger()  # 关掉
        assert not window._settings.translate_use_glossary
        assert window._glossary_for("Attention helps.") == []
