"""划词翻译：「译」按钮 / 直接翻译、浮窗流式译文、连翻、乱码 → 截图识别、收起时机；
查词卡（本机词典）与单词本收藏。

网络走 ``httpx.MockTransport``（复用 AI 助读测试的假服务），不访问真实网络。
"""

from __future__ import annotations

from pathlib import Path
from threading import Event

import httpx
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt, QThread, Signal
from PySide6.QtWidgets import QWidget

from pdfreader.controllers import DocumentController
from pdfreader.engines.types import Rect, WordBox
from pdfreader.gui import selection_translator as st_module
from pdfreader.gui.ai_assistant import AiAssistant
from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.gui.selection_translator import MAX_CONTINUOUS_CHARS, SelectionTranslator
from pdfreader.gui.widgets.pdf_view import PdfView
from pdfreader.services.ai.provider import AiSettings, AiSettingsStore
from pdfreader.services.materials import MaterialStore
from tests.unit.gui.test_ai_assistant import FakeServer, _configured, _make_doc


def _words(*lines: str) -> tuple[WordBox, ...]:
    """每个参数一行；行内按空格切词，词宽按字数估算。"""
    out: list[WordBox] = []
    for li, line in enumerate(lines):
        x = 72.0
        for wi, word in enumerate(line.split()):
            width = 6.0 * len(word)
            out.append(WordBox(Rect(x, 100 + 20 * li, x + width, 112 + 20 * li), word, 0, li, wi))
            x += width + 4
    return tuple(out)


class Rig:
    def __init__(self, qtbot, tmp_path: Path, settings: AiSettings, server: FakeServer) -> None:  # type: ignore[no-untyped-def]
        self.host = QWidget()
        qtbot.addWidget(self.host)
        self.controller = DocumentController()
        self.controller.open_file(_make_doc(tmp_path / "doc.pdf"))
        self.panel = AiPanel()
        qtbot.addWidget(self.panel)
        store = AiSettingsStore(tmp_path / "ai.json")
        store.save(settings)
        self.ai = AiAssistant(
            self.host,
            self.controller,
            self.panel,
            current_page=lambda: 0,
            navigate=lambda p, y: None,
            show_panel=lambda: None,
            notify=lambda m: None,
            on_annotation_added=lambda p: None,
            store=store,
            client_factory=server.factory,
        )
        self.ai.confirm = lambda *a, **k: (True, False)  # type: ignore[method-assign]
        self.server = server
        self.lines: tuple[str, ...] = ("Attention is all you need",)
        self.view = PdfView()
        qtbot.addWidget(self.view)
        self.view.resize(700, 600)
        self.view.set_text_provider(lambda _p: _words(*self.lines))
        self.view.setup_document(1, [(595, 842)])
        self.view.set_zoom(1.0)
        self.view.show()
        qtbot.waitExposed(self.view)
        self.notes: list[str] = []
        self.materials = MaterialStore(tmp_path / "materials.db")
        self.tr = SelectionTranslator(
            self.view,
            self.ai,
            notify=self.notes.append,
            materials=self.materials,
            doc_info=lambda: (str(tmp_path / "doc.pdf"), "Doc"),
        )

    def select(self, *lines: str) -> None:
        """选中给定的几行文字，并像松开鼠标那样发 selectionFinished。"""
        self.lines = lines
        self.view.invalidate_page(0)
        last = _words(*lines)[-1].rect  # 拖到最后一个词上（选择按「离哪个词最近」取端点）
        end = QPointF((last.x0 + last.x1) / 2, (last.y0 + last.y1) / 2)
        self.view._select_range(0, QPointF(72, 100), end)
        self.view.selectionFinished.emit()

    def wait(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        qtbot.waitUntil(lambda: not self.ai.workers(), timeout=5000)

    def close(self) -> None:
        self.tr.shutdown()
        self.ai.shutdown()
        self.materials.close()
        self.controller.close_document()


@pytest.fixture
def rig(qtbot, tmp_path):  # type: ignore[no-untyped-def]
    made: list[Rig] = []

    def factory(settings: AiSettings | None = None, server: FakeServer | None = None) -> Rig:
        r = Rig(qtbot, tmp_path, settings or _configured(), server or FakeServer("译文"))
        made.append(r)
        return r

    yield factory
    for r in made:
        r.close()


def _trigger_quick(r: Rig, kind: str) -> None:
    """经过三个真实入口发请求；查词使用本机词典没有收录的词。"""
    if kind == "image":
        r.tr.translate_area(0, Rect(60, 60, 400, 140))
    else:
        r.select("hyperparameterization" if kind == "word" else "Attention is all you need")
        r.tr.translate_selection()


@pytest.mark.parametrize("kind", ["text", "word", "image"])
class TestQuickLifecycle:
    @pytest.mark.parametrize("status", [200, 401])
    def test_fast_reply_before_quick_method_returns_is_not_lost(  # type: ignore[no-untyped-def]
        self, qtbot, rig, monkeypatch, kind: str, status: int
    ) -> None:
        """用线程完成屏障强制快响应早于 quick 方法返回，不靠负载或 sleep。"""
        server = FakeServer("注意力就是一切")
        server.status = status
        r = rig(settings=_configured(vision=True), server=server)
        method = "quick_word" if kind == "word" else "quick_translate"
        original = getattr(r.ai, method)

        def finish_before_return(*args, **kwargs):  # type: ignore[no-untyped-def]
            worker = original(*args, **kwargs)
            assert worker is not None and worker.wait(5000)
            return worker

        monkeypatch.setattr(r.ai, method, finish_before_return)
        _trigger_quick(r, kind)
        r.wait(qtbot)
        assert len(server.requests) == 1
        assert r.tr.popup.isVisible()
        if status == 200:
            assert r.tr.popup.answer == "注意力就是一切"
            assert "注意力就是一切" in r.tr.popup.body.text()
            assert r.tr.popup.actions.isVisibleTo(r.tr.popup)
        else:
            assert r.tr.popup.answer == ""
            assert r.tr.popup.notice.isVisibleTo(r.tr.popup)
            assert "⚠️" in r.tr.popup.notice.text()
            assert "API Key" in r.tr.popup.notice.text()
        assert r.tr._worker is None and not r.ai.workers()

    def test_privacy_cancel_does_not_prepare_popup_or_start_request(  # type: ignore[no-untyped-def]
        self, rig, monkeypatch, kind: str
    ) -> None:
        r = rig(settings=_configured(vision=True))
        monkeypatch.setattr(r.ai, "confirm", lambda *args, **kwargs: (False, False))
        _trigger_quick(r, kind)
        assert not r.server.requests
        assert not r.tr.popup.isVisible()
        assert r.tr._worker is None and not r.ai.workers()

    def test_reset_ignores_response_from_cancelled_request(  # type: ignore[no-untyped-def]
        self, qtbot, rig, kind: str
    ) -> None:
        class HeldServer(FakeServer):
            def __init__(self) -> None:
                super().__init__("已取消请求的结果")
                self.entered = Event()
                self.release = Event()

            def handler(self, request: httpx.Request) -> httpx.Response:
                self.entered.set()
                assert self.release.wait(5)
                return super().handler(request)

        server = HeldServer()
        r = rig(settings=_configured(vision=True), server=server)
        try:
            _trigger_quick(r, kind)
            qtbot.waitUntil(server.entered.is_set, timeout=3000)
            assert r.tr.popup.isVisible() and r.tr._worker is not None
            r.tr.reset()
            assert not r.tr.popup.isVisible() and r.tr._worker is None
        finally:
            server.release.set()
        r.wait(qtbot)
        assert not r.tr.popup.isVisible()
        assert r.tr.popup.answer == ""


class TestTrigger:
    def test_button_mode_shows_pill_then_translates(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(server=FakeServer("注意力就是一切"))
        r.select("Attention is all", "you need")
        assert r.tr.pill.isVisible()
        assert not r.server.requests  # 只显示按钮，不发送
        qtbot.mouseClick(r.tr.pill, Qt.MouseButton.LeftButton)
        r.wait(qtbot)
        assert r.server.last_user() == "Attention is all you need"  # 合并了换行
        assert r.tr.popup.isVisible() and not r.tr.pill.isVisible()
        assert "注意力就是一切" in r.tr.popup.body.text()
        assert r.tr.popup.actions.isVisibleTo(r.tr.popup)
        # 不进 AI 面板，也不进对话历史
        assert not r.panel.turns and not r.ai._history

    def test_ligatures_cleaned_before_sending(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select("an eﬃcient ﬁne-tuning")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "an efficient fine-tuning"

    def test_no_pill_when_ai_not_ready(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(settings=AiSettings(enabled=False))
        r.select("Attention please")
        assert not r.tr.pill.isVisible()

    def test_off_mode_does_nothing(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.tr.set_mode("off")
        r.select("Attention")
        assert not r.tr.pill.isVisible() and not r.tr.popup.isVisible()

    def test_auto_mode_translates_after_release(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.tr.set_mode("auto")
        r.select("Attention is all you need")
        assert not r.tr.pill.isVisible()
        qtbot.waitUntil(lambda: bool(r.server.requests), timeout=3000)
        r.wait(qtbot)
        assert r.tr.popup.isVisible()

    def test_glossary_sent_with_translation(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.ai._glossary = lambda text: [("attention", "注意力")] if "Attention" in text else []
        r.select("Attention please")
        r.tr.translate_selection()
        r.wait(qtbot)
        system = r.server.requests[-1]["body"]["messages"][0]["content"]
        assert "术语表" in system and "- attention → 注意力" in system

    def test_failure_shown_in_popup(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer()
        server.status = 401
        r = rig(server=server)
        r.select("Attention please")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.tr.popup.notice.isVisibleTo(r.tr.popup)
        assert "⚠️" in r.tr.popup.notice.text()


class TestGarbled:
    def test_garbled_text_not_sent(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select(" ")
        r.tr.translate_selection()
        assert not r.server.requests
        assert r.tr.popup.isVisible()
        assert r.tr.popup.capture_btn.isVisibleTo(r.tr.popup)
        with qtbot.waitSignal(r.tr.captureRequested):
            r.tr.popup.capture_btn.click()
        assert not r.tr.popup.isVisible()

    def test_area_with_vision_model_sends_image(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(settings=_configured(vision=True))
        r.tr.translate_area(0, Rect(60, 60, 400, 140))
        r.wait(qtbot)
        content = r.server.last_user()
        assert isinstance(content, list)
        assert any(part.get("type") == "image_url" for part in content)
        assert "译文" in r.tr.popup.body.text()

    def test_area_without_vision_uses_local_ocr(  # type: ignore[no-untyped-def]
        self, qtbot, rig, monkeypatch
    ) -> None:
        class FakeOcr(QThread):
            recognized = Signal(str)
            failed = Signal(str)

            def __init__(self, png: bytes, parent=None) -> None:  # type: ignore[no-untyped-def]
                super().__init__(parent)
                assert png.startswith(b"\x89PNG")

            def run(self) -> None:
                self.recognized.emit("Recognized\nline")

        monkeypatch.setattr(st_module, "ImageOcrWorker", FakeOcr)
        monkeypatch.setattr(st_module, "OCR_AVAILABLE", True)
        r = rig()
        r.tr.translate_area(0, Rect(60, 60, 400, 140))
        qtbot.waitUntil(lambda: bool(r.server.requests), timeout=3000)
        r.wait(qtbot)
        qtbot.waitUntil(lambda: not r.tr.busy(), timeout=3000)
        assert r.server.last_user() == "Recognized line"

    def test_area_without_vision_or_ocr_explains(self, qtbot, rig, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(st_module, "OCR_AVAILABLE", False)
        r = rig()
        r.tr.translate_area(0, Rect(60, 60, 400, 140))
        assert not r.server.requests
        assert "支持图片输入" in r.tr.popup.notice.text()


class TestContinuous:
    def test_segments_joined(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.tr.set_continuous(True)
        r.select("The model reads the")
        r.tr.translate_selection()
        r.wait(qtbot)
        r.select("whole sentence.")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "The model reads the whole sentence."
        assert "已拼接 2 段" in r.tr.popup.source.text()
        # 同一段再点一次不重复拼接
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "The model reads the whole sentence."

    def test_off_starts_fresh(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select("first part")
        r.tr.translate_selection()
        r.wait(qtbot)
        r.select("second part")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "second part"

    def test_length_cap_restarts(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.tr.set_continuous(True)
        r.tr._segments = ["x" * MAX_CONTINUOUS_CHARS]
        r.tr.popup.show()
        r.select("tail")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "tail"

    def test_toggle_from_popup_reports(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        with qtbot.waitSignal(r.tr.continuousChanged) as blocker:
            r.tr.popup.chain_btn.click()
        assert blocker.args == [True]
        assert r.notes and "连翻已开启" in r.notes[-1]


class TestDismiss:
    def _open(self, qtbot, r: Rig) -> None:  # type: ignore[no-untyped-def]
        r.select("Attention please")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.tr.popup.isVisible()

    def test_click_elsewhere_hides_unpinned(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        self._open(qtbot, r)
        qtbot.mouseClick(r.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert not r.tr.popup.isVisible()

    def test_pinned_stays(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        self._open(qtbot, r)
        r.tr.popup.pin_btn.click()
        qtbot.mouseClick(r.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        assert r.tr.popup.isVisible()

    def test_scroll_hides_pill(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select("Attention")
        assert r.tr.pill.isVisible()
        r.view.scrolled.emit()
        assert not r.tr.pill.isVisible()

    def test_reset_hides_everything(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        self._open(qtbot, r)
        r.tr.reset()
        assert not r.tr.popup.isVisible() and not r.tr.pill.isVisible()

    def test_continue_in_panel(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        self._open(qtbot, r)
        r.tr.popup.panel_btn.click()
        qtbot.waitUntil(lambda: bool(r.panel.turns) and not r.ai.busy, timeout=5000)
        r.wait(qtbot)
        assert r.panel.turns[-1].quote == "Attention please"
        assert r.ai._history  # 面板里的翻译会进对话历史


class TestWordCard:
    def test_single_word_shows_card_offline(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(settings=AiSettings(enabled=False))  # 不需要 AI
        r.select("Attention,")
        assert r.tr.pill.isVisible()  # 查词不需要 AI，也出现「译」
        r.tr.translate_selection()
        card = r.tr.popup.word_card
        assert r.tr.popup.isVisible() and r.tr.popup.word_mode
        assert card.word.text() == "attention" and "/" in card.phonetic.text()
        assert "注意" in card.senses.text()
        assert card.explain_btn.isHidden()  # AI 未配置：不给「结合本句解释」
        assert not r.server.requests

    def test_inflection_note(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select("went")
        r.tr.translate_selection()
        card = r.tr.popup.word_card
        assert card.word.text() == "go" and card.note.text() == "went 是 go 的过去式"

    def test_save_and_unsave(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.select("gradient")
        r.tr.translate_selection()
        card = r.tr.popup.word_card
        with qtbot.waitSignal(r.tr.materialsChanged):
            card.save_btn.click()
        saved = r.materials.find("word", "gradient")
        assert saved is not None and saved.doc_title == "Doc" and saved.page == 0
        assert saved.extra.get("phonetic") and "梯度" in saved.note
        assert card.save_btn.isChecked() and card.save_btn.text() == "已收藏"
        # 再查同一个词：显示为已收藏；再点取消
        r.tr.translate_selection()
        assert card.save_btn.isChecked()
        card.save_btn.click()
        assert r.materials.find("word", "gradient") is None and not card.save_btn.isChecked()

    def test_explain_in_context(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(server=FakeServer("**本句中**：梯度"))
        r.select("gradient")
        r.tr.translate_selection()
        assert not r.tr.popup.word_card.explain_btn.isHidden()
        r.tr.popup.word_card.explain_btn.click()
        r.wait(qtbot)
        assert "请解释英文单词「gradient」" in r.server.last_user()
        assert "它所在的句子" in r.server.last_user()  # 带上了所在句子
        assert not r.tr.popup.word_card.isHidden()  # 词卡还在，解释接在下面
        assert "梯度" in r.tr.popup.body.text()

    def test_unknown_word_goes_to_ai(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(server=FakeServer("超参数"))
        r.select("hyperparameterization")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert "「hyperparameterization」" in r.server.last_user()
        assert r.tr.popup.word_card.isHidden()
        assert "hyperparameterization" in r.tr.popup.source.text()

    def test_unknown_word_without_ai(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig(settings=AiSettings(enabled=False))
        r.select("hyperparameterization")
        r.tr.translate_selection()
        assert not r.server.requests
        assert "词典里没有" in r.tr.popup.notice.text()

    def test_continuous_appends_single_word(self, qtbot, rig) -> None:  # type: ignore[no-untyped-def]
        r = rig()
        r.tr.set_continuous(True)
        r.select("The method computes the")
        r.tr.translate_selection()
        r.wait(qtbot)
        r.select("gradient.")
        r.tr.translate_selection()
        r.wait(qtbot)
        assert r.server.last_user() == "The method computes the gradient."
        assert not r.tr.popup.word_mode


# ---------------------------------------------------------------- 主窗口接线
@pytest.fixture
def window(qtbot, monkeypatch):  # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QMessageBox

    from pdfreader.gui.main_window import MainWindow

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
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


class TestMainWindowWiring:
    def test_mode_menu_applies_to_every_tab_and_persists(  # type: ignore[no-untyped-def]
        self, window, tmp_path: Path
    ) -> None:
        window.open_path(_make_doc(tmp_path / "a.pdf"))
        window.open_path(_make_doc(tmp_path / "b.pdf"))
        window._selection_translate_actions["auto"].trigger()
        assert window._settings.selection_translate == "auto"
        assert all(t.translator.mode == "auto" for t in window.tabs)
        assert window._selection_translate_actions["auto"].isChecked()
        assert not window._selection_translate_actions["button"].isChecked()

    def test_continuous_shortcut_syncs_tabs(self, window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "a.pdf"))
        window.open_path(_make_doc(tmp_path / "b.pdf"))
        window.translate_continuous_action.trigger()
        assert window._settings.translate_continuous
        assert all(t.translator.continuous for t in window.tabs)
        # 浮窗上关掉：功能区按钮与其他标签跟着变
        window.current_tab.translator.popup.chain_btn.click()
        assert not window.translate_continuous_action.isChecked()
        assert not any(t.translator.continuous for t in window.tabs)

    def test_garbled_capture_routes_to_translator(  # type: ignore[no-untyped-def]
        self, window, tmp_path: Path, monkeypatch
    ) -> None:
        from pdfreader.controllers import AnnotationMode

        window.open_path(_make_doc(tmp_path / "a.pdf"))
        tab = window.current_tab
        areas: list[tuple[int, Rect]] = []
        explained: list[tuple[int, Rect]] = []
        monkeypatch.setattr(tab.translator, "translate_area", lambda p, r: areas.append((p, r)))
        monkeypatch.setattr(tab.ai, "explain_area", lambda p, r: explained.append((p, r)))
        tab.translator.captureRequested.emit()
        assert window._annotation_controller.mode == AnnotationMode.CAPTURE
        window.pdf_view.areaCaptured.emit(0, Rect(1, 2, 30, 40))
        assert areas == [(0, Rect(1, 2, 30, 40))] and not explained
        # 之后普通的「框选提问」仍交给 AI 解释
        window._start_capture()
        window.pdf_view.areaCaptured.emit(0, Rect(1, 2, 30, 40))
        assert len(explained) == 1 and len(areas) == 1

    def test_switching_tool_cancels_translate_capture(  # type: ignore[no-untyped-def]
        self, window, tmp_path: Path, monkeypatch
    ) -> None:
        from pdfreader.controllers import AnnotationMode

        window.open_path(_make_doc(tmp_path / "a.pdf"))
        tab = window.current_tab
        explained: list[tuple[int, Rect]] = []
        monkeypatch.setattr(tab.ai, "explain_area", lambda p, r: explained.append((p, r)))
        tab.translator.captureRequested.emit()
        window._annotation_controller.set_mode(AnnotationMode.HIGHLIGHT)
        window.capture_action.trigger()  # 再从功能区进入框选：应当是「解释」
        window.pdf_view.areaCaptured.emit(0, Rect(1, 2, 30, 40))
        assert len(explained) == 1
