"""AI 助读界面：面板渲染、设置对话框、编排（确认 / 流式 / 存笔记 / 框选）与主窗口接线。

网络一律走 ``httpx.MockTransport``，Key 存在测试用的内存凭据库（conftest）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pymupdf
import pytest
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMessageBox, QWidget

from pdfreader.controllers import AnnotationMode, DocumentController
from pdfreader.engines.types import AnnotationType, Rect
from pdfreader.gui.ai_assistant import LARGE_SEND_CHARS, AiAssistant
from pdfreader.gui.dialogs.ai_settings_dialog import AiSettingsDialog
from pdfreader.gui.main_window import MainWindow
from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.services.ai.client import ChatClient
from pdfreader.services.ai.keystore import default_keystore
from pdfreader.services.ai.provider import AiSettings, AiSettingsStore, new_provider


# ---------------------------------------------------------------- 工具
def _sse(text: str) -> bytes:
    parts = [
        {"choices": [{"delta": {"content": ch}}]}
        for ch in (text[: len(text) // 2], text[len(text) // 2 :])
    ]
    lines = [f"data: {json.dumps(p, ensure_ascii=False)}\n\n" for p in parts]
    return ("".join(lines) + "data: [DONE]\n\n").encode("utf-8")


class FakeServer:
    """记录请求、按队列回答的假服务。"""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers) or ["好的"]
        self.requests: list[dict[str, Any]] = []
        self.status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m-b"}, {"id": "m-a"}]})
        body = json.loads(request.content)
        self.requests.append({"body": body, "auth": request.headers.get("authorization", "")})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        text = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse(text)
        )

    def factory(self, base: str, key: str) -> ChatClient:
        return ChatClient(base, key, transport=httpx.MockTransport(self.handler))

    def last_user(self) -> Any:
        return self.requests[-1]["body"]["messages"][-1]["content"]


def _make_doc(path: Path, pages: int = 3) -> Path:
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 80), f"Section {i + 1} attention mechanism", fontsize=14)
        p.insert_text((72, 120), f"Body text of page {i + 1} about transformers.", fontsize=11)
    d.save(str(path))
    d.close()
    return path


def _configured(vision: bool = False, *, preset: str = "deepseek") -> AiSettings:
    provider = new_provider(preset)
    provider.model = provider.model or "m"
    provider.vision = vision
    default_keystore().set(provider.id, "sk-test-123")
    return AiSettings(enabled=True, active_id=provider.id, providers=[provider])


class Harness:
    def __init__(self, qtbot, tmp_path: Path, settings: AiSettings, server: FakeServer) -> None:  # type: ignore[no-untyped-def]
        self.host = QWidget()
        qtbot.addWidget(self.host)
        self.controller = DocumentController()
        self.panel = AiPanel()
        qtbot.addWidget(self.panel)
        self.store = AiSettingsStore(tmp_path / "ai.json")
        self.store.save(settings)
        self.messages: list[str] = []
        self.navigated: list[tuple[int, float | None]] = []
        self.added: list[int] = []
        self.marks: dict[int, list[Rect]] = {}
        self.shown = 0
        self.ai = AiAssistant(
            self.host,
            self.controller,
            self.panel,
            current_page=lambda: 0,
            navigate=lambda p, y: self.navigated.append((p, y)),
            show_panel=self._show,
            notify=self.messages.append,
            on_annotation_added=self.added.append,
            show_marks=self._set_marks,
            store=self.store,
            client_factory=server.factory,
        )
        self.confirms: list[str] = []
        self.answer: tuple[bool, bool] = (True, False)
        self.ai.confirm = self._confirm  # type: ignore[method-assign]
        self.controller.open_file(_make_doc(tmp_path / "doc.pdf"))

    def _show(self) -> None:
        self.shown += 1

    def _set_marks(self, page: int, rects) -> None:  # type: ignore[no-untyped-def]
        if rects:
            self.marks[page] = list(rects)
        else:
            self.marks.pop(page, None)

    def _confirm(self, title: str, text: str, *, remember: str | None = None) -> tuple[bool, bool]:
        self.confirms.append(title)
        return self.answer

    def wait(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        qtbot.waitUntil(lambda: not self.panel.busy and not self.ai.busy, timeout=5000)
        qtbot.waitUntil(lambda: not self.ai.workers(), timeout=5000)

    def close(self) -> None:
        self.ai.shutdown()
        if self.controller.has_document:
            self.controller.close_document()


@pytest.fixture
def make(qtbot, tmp_path):  # type: ignore[no-untyped-def]
    made: list[Harness] = []

    def factory(settings: AiSettings | None = None, server: FakeServer | None = None) -> Harness:
        h = Harness(qtbot, tmp_path, settings or _configured(), server or FakeServer())
        made.append(h)
        return h

    yield factory
    for h in made:
        h.close()


# ---------------------------------------------------------------- 面板
class TestPanel:
    def test_unconfigured_shows_guide(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = AiPanel()
        qtbot.addWidget(panel)
        panel.set_configured(False)
        assert not panel.chat.empty.isHidden() and not panel.chat.empty.setup_btn.isHidden()
        assert "配置模型" in panel.chat.empty.setup_btn.text()
        assert not panel.summary_btn.isEnabled()
        with qtbot.waitSignal(panel.settingsRequested):
            panel._on_anchor(QUrl("action:settings"))

    def test_turn_lifecycle_and_citations(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = AiPanel()
        qtbot.addWidget(panel)
        panel.set_configured(True)
        panel.set_page_count(5)
        panel.begin_turn("提问", "什么是注意力？")
        assert panel.busy
        panel.append_reasoning("嗯")
        panel.append_delta("见 [p.2]")
        panel.finish_turn()
        assert not panel.busy
        assert panel.last_answer() == "见 [p.2]"
        assert "(cite:2)" in panel.transcript_markdown()
        with qtbot.waitSignal(panel.citationClicked) as blocker:
            panel._on_anchor(QUrl("cite:2"))
        assert blocker.args == [2]
        user, ai = panel.chat.bubbles[-1]
        assert user.context is not None and "什么是注意力？" in user.context.text
        with qtbot.waitSignal(panel.saveNoteRequested):
            ai.note_btn.click()

    def test_questions_fail_cancel_clear(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = AiPanel()
        qtbot.addWidget(panel)
        panel.set_configured(True)
        panel.begin_turn("推荐问题")
        panel.finish_turn("", questions=["问题一？", "问题二？"])
        with qtbot.waitSignal(panel.askRequested) as blocker:
            panel._on_anchor(QUrl("ask:1"))
        assert blocker.args == ["问题二？"]
        panel.begin_turn("x")
        panel.fail_turn("API Key 无效")
        assert "API Key 无效" in panel.transcript_text()
        panel.begin_turn("y")
        panel.cancel_turn()
        assert "已停止" in panel.transcript_text()
        panel.clear()
        assert panel.turns == []

    def test_send_and_stop_button(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        panel = AiPanel()
        qtbot.addWidget(panel)
        panel.set_configured(True)
        panel.prefill("关于「x」：")
        assert panel.input.toPlainText() == "关于「x」："
        with qtbot.waitSignal(panel.askRequested) as blocker:
            panel.send_btn.click()
        assert blocker.args == ["关于「x」："]
        panel.begin_turn("t")
        with qtbot.waitSignal(panel.stopRequested):
            panel.send_btn.click()


# ---------------------------------------------------------------- 设置对话框
class TestSettingsDialog:
    def test_add_fill_accept_stores_key_outside_json(self, qtbot, tmp_path) -> None:  # type: ignore[no-untyped-def]
        keystore = default_keystore()
        dialog = AiSettingsDialog(AiSettings(), keystore)
        qtbot.addWidget(dialog)
        provider = dialog.add_provider("deepseek")
        dialog.key_edit.setText("sk-secret-999")
        dialog.model_combo.setCurrentText("deepseek-chat")
        assert dialog.enabled_check.isChecked()  # 添加第一个服务时自动启用
        dialog.accept()
        settings = dialog.result_settings()
        assert settings.enabled and settings.active_id == provider.id
        assert keystore.get(provider.id) == "sk-secret-999"
        store = AiSettingsStore(tmp_path / "ai.json")
        store.save(settings)
        assert "sk-secret-999" not in (tmp_path / "ai.json").read_text(encoding="utf-8")

    def test_fetch_models_and_test_connection(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("OK")
        dialog = AiSettingsDialog(AiSettings(), default_keystore(), client_factory=server.factory)
        qtbot.addWidget(dialog)
        dialog.add_provider("custom")
        dialog.url_edit.setText("https://api.example.com/v1/chat/completions")
        dialog.key_edit.setText("k")
        dialog.fetch_models()
        qtbot.waitUntil(lambda: "获取到" in dialog.status_label.text(), timeout=5000)
        assert dialog.model_combo.count() == 2
        assert dialog._current is not None
        assert dialog._current.base_url == "https://api.example.com/v1"
        qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
        dialog.test_connection()
        qtbot.waitUntil(lambda: "连接成功" in dialog.status_label.text(), timeout=5000)
        qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
        assert server.requests[-1]["body"]["max_tokens"] == 16

    def test_test_connection_failure_and_remove(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer()
        server.status = 401
        settings = _configured()
        dialog = AiSettingsDialog(settings, default_keystore(), client_factory=server.factory)
        qtbot.addWidget(dialog)
        dialog.test_connection()
        qtbot.waitUntil(lambda: "✗" in dialog.status_label.text(), timeout=5000)
        assert "API Key" in dialog.status_label.text()
        qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
        removed = settings.providers[0].id
        dialog.remove_current()
        assert not dialog.form_host.isEnabled()
        dialog.accept()
        assert dialog.result_settings().providers == []
        assert default_keystore().get(removed) == ""

    def test_missing_url_or_model(self, qtbot) -> None:  # type: ignore[no-untyped-def]
        dialog = AiSettingsDialog(AiSettings(), default_keystore())
        qtbot.addWidget(dialog)
        dialog.add_provider("custom")
        dialog.url_edit.setText("")
        dialog.fetch_models()
        assert "Base URL" in dialog.status_label.text()
        dialog.url_edit.setText("http://localhost:1/v1")
        dialog.model_combo.setCurrentText("")
        dialog.test_connection()
        assert "模型" in dialog.status_label.text()
        dialog.reject()


# ---------------------------------------------------------------- 编排
class TestAssistant:
    def test_translate_streams_into_panel(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("注意力机制")
        h = make(server=server)
        h.ai.translate("attention mechanism", 0, Rect(72, 66, 200, 82))
        h.wait(qtbot)
        assert h.panel.last_answer() == "注意力机制"
        assert server.requests[0]["auth"] == "Bearer sk-test-123"
        assert "attention mechanism" in server.last_user()
        assert h.confirms == ["发送到 AI 服务"]  # 首次外发征得同意
        assert h.shown >= 1

    def test_consent_remembered(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        h = make()
        h.answer = (True, True)
        h.ai.translate("a")
        h.wait(qtbot)
        assert h.ai.settings.active_id in h.store.load().consented
        h.ai.translate("b")
        h.wait(qtbot)
        assert h.confirms == ["发送到 AI 服务"]

    def test_declined_consent_sends_nothing(self, make) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer()
        h = make(server=server)
        h.answer = (False, False)
        h.ai.explain("x", 0, Rect(72, 66, 200, 82))
        assert server.requests == [] and not h.panel.turns

    def test_local_provider_skips_consent(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        settings = _configured(preset="ollama")
        h = make(settings=settings)
        h.ai.translate("x")
        h.wait(qtbot)
        assert h.confirms == []

    def test_unconfigured_offers_settings(self, make, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        h = make(settings=AiSettings())
        opened: list[bool] = []
        monkeypatch.setattr(h.ai, "open_settings", lambda: opened.append(True) or False)
        h.ai.summarize()
        assert h.confirms == ["AI 助读未配置"] and opened == [True]
        assert not h.panel.turns

    def test_ask_keeps_history_and_cites(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("答一 [p.2]", "答二")
        h = make(server=server)
        h.ai.ask("What is attention?")
        h.wait(qtbot)
        h.ai.ask("And transformers?")
        h.wait(qtbot)
        messages = server.requests[-1]["body"]["messages"]
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user"]
        assert messages[1]["content"] == "What is attention?"
        h.panel.citationClicked.emit(2)
        assert h.navigated == [(1, None)]

    def test_ask_sends_attached_quote(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("答")
        h = make(server=server)
        h.panel.attach_context("quadratic cost of self-attention", "第 2 页")
        h.ai.ask("为什么是二次方？")
        h.wait(qtbot)
        sent = server.last_user()
        assert "用户引用的原文" in sent and "quadratic cost of self-attention" in sent
        assert h.panel.attachment() == ("", "")  # 发出后引用收起
        turn = h.panel.turns[-1]
        assert turn.title == "为什么是二次方？" and turn.quote.startswith("quadratic")
        assert turn.label == "第 2 页"
        h.ai.ask("再举个例子")
        h.wait(qtbot)
        messages = server.requests[-1]["body"]["messages"]
        assert "quadratic cost" in messages[1]["content"]  # 引用写进了历史
        assert "用户引用的原文" not in messages[-1]["content"]

    def test_translate_then_follow_up(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("注意力", "答")
        h = make(server=server)
        h.ai.translate("attention", 0, Rect(72, 66, 200, 82))
        h.wait(qtbot)
        turn = h.panel.turns[-1]
        assert turn.title.startswith("翻译成") and turn.quote == "attention"
        assert turn.label == "第 1 页"
        h.ai.ask("这个词还有别的译法吗？")
        h.wait(qtbot)
        messages = server.requests[-1]["body"]["messages"]
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert "attention" in messages[1]["content"] and messages[2]["content"] == "注意力"

    def test_summary_and_preset(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("**研究问题** …")
        h = make(server=server)
        h.panel.actionRequested.emit("summary")
        h.wait(qtbot)
        assert "Section 1" in server.last_user()
        h.panel.actionRequested.emit("limits")
        h.wait(qtbot)
        assert h.panel.turns[-1].title == "找出局限"

    def test_suggest_parses_questions(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        h = make(server=FakeServer("1. 问题一？\n2. 问题二？"))
        h.ai.suggest()
        h.wait(qtbot)
        assert h.panel.turns[-1].questions == ["问题一？", "问题二？"]

    def test_failure_shown(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer()
        server.status = 429
        h = make(server=server)
        h.ai.translate("x")
        h.wait(qtbot)
        assert "额度" in h.panel.turns[-1].error

    def test_save_note_anchored_at_selection(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        h = make(server=FakeServer("译文"))
        h.ai.translate("x", 1, Rect(72, 100, 200, 120))
        h.wait(qtbot)
        h.panel.chat.bubbles[-1][1].note_btn.click()
        assert h.added == [1]
        notes = [a for a in h.controller.list_annotations(1) if a.content]
        assert notes and notes[0].content.startswith("【AI · ")

    def test_explain_area_vision_sends_image(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("这是公式")
        h = make(settings=_configured(vision=True), server=server)
        h.ai.explain_area(0, Rect(60, 60, 300, 130))
        h.wait(qtbot)
        content = server.last_user()
        image = next(p for p in content if p.get("type") == "image_url")
        assert image["image_url"]["url"].startswith("data:image/png;base64,")

    def test_explain_area_text_fallback(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        server = FakeServer("解释")
        h = make(server=server)
        h.ai.explain_area(0, Rect(60, 60, 400, 90))
        h.wait(qtbot)
        assert "attention" in server.last_user()
        h.ai.explain_area(0, Rect(400, 700, 500, 780))  # 空白处：没有文字可发
        assert any("没有可提取的文字" in m for m in h.messages)

    def test_large_send_warning(self, make, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        h = make()
        h.ai.settings.consented.append(h.ai.settings.active_id)
        h.answer = (False, False)
        h.ai.translate("x" * (LARGE_SEND_CHARS + 10))
        assert h.confirms == ["内容较多"] and not h.panel.turns

    def test_stop_and_reset(self, make, qtbot) -> None:  # type: ignore[no-untyped-def]
        h = make()
        h.ai.settings.consented.append(h.ai.settings.active_id)
        h.ai.translate("x")
        h.ai.stop()
        assert not h.panel.busy and h.panel.turns[-1].cancelled
        h.wait(qtbot)
        h.ai.reset_document()
        assert h.panel.turns == []

    def test_no_document_and_empty_selection(self, make) -> None:  # type: ignore[no-untyped-def]
        h = make()
        h.ai.translate("  ")
        h.controller.close_document()
        h.ai.summarize()
        assert "请先选中要翻译的文字" in h.messages and "请先打开文档" in h.messages


# ---------------------------------------------------------------- 主窗口接线
@pytest.fixture
def window(qtbot, monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 820)
    w.show()
    qtbot.waitExposed(w)
    yield w
    w._ai.shutdown()
    if w._controller.document is not None:
        w._controller.document.mark_saved()
        w._controller.close_document()


class TestMainWindowWiring:
    def test_panel_toggle_and_capture_mode(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_doc(tmp_path / "w.pdf"))
        window.ai_panel_action.trigger()
        assert window.ai_panel.isVisible() and window.ai_panel_action.isChecked()
        window.ai_panel.closeRequested.emit()
        assert not window.ai_panel_action.isChecked()
        assert window.capture_action.isEnabled()
        window.ai_panel.captureRequested.emit()
        assert window._annotation_controller.mode == AnnotationMode.CAPTURE
        called: list[tuple[int, Rect]] = []
        window._ai.explain_area = lambda p, r: called.append((p, r))  # type: ignore[method-assign]
        window.pdf_view.areaCaptured.emit(0, Rect(1, 2, 30, 40))
        assert called == [(0, Rect(1, 2, 30, 40))]
        assert window._annotation_controller.mode == AnnotationMode.NONE

    def test_context_menu_requests_reach_assistant(
        self, window: MainWindow, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        window.open_path(_make_doc(tmp_path / "c.pdf"))
        calls: list[tuple[str, str]] = []
        monkeypatch.setattr(window._ai, "translate", lambda t, p, r: calls.append(("t", t)))
        monkeypatch.setattr(window._ai, "explain", lambda t, p, r: calls.append(("e", t)))
        monkeypatch.setattr(
            window, "_selection_context", lambda: ("attention", 0, Rect(72, 66, 200, 82))
        )
        window.pdf_view.aiRequested.emit("translate")
        window.pdf_view.aiRequested.emit("explain")
        window.pdf_view.aiRequested.emit("ask")
        assert calls == [("t", "attention"), ("e", "attention")]
        assert window.ai_panel.attachment() == ("attention", "第 1 页")

    def test_document_switch_resets_conversation(self, window: MainWindow, tmp_path: Path) -> None:
        window.open_path(_make_doc(tmp_path / "a.pdf"))
        window.ai_panel.begin_turn("x")
        window.ai_panel.finish_turn("y")
        window.open_path(_make_doc(tmp_path / "b.pdf"))
        assert window.ai_panel.turns == []


# ---------------------------------------------------------------- 扫描件：OCR 结果直接给 AI
def _blank_doc(path: Path, pages: int = 2) -> Path:
    d = pymupdf.open()
    for _ in range(pages):
        d.new_page()
    d.save(str(path))
    d.close()
    return path


def test_scanned_doc_offers_ocr_then_uses_ocr_text(make, qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("回答")
    h = make(server=server)
    h.controller.close_document()
    h.controller.open_file(_blank_doc(tmp_path / "scan.pdf"))
    h.ai.reset_document()
    started: list[bool] = []
    h.ai._start_ocr = lambda: started.append(True)

    h.ai.ask("这篇讲什么？")
    assert "没有可读的文字" in h.confirms and started == [True]
    assert server.requests == []  # 没有文字就不外发

    h.ai.add_ocr_text(1, "扫描页识别出的内容 XYZ-42")
    h.ai.ask("这篇讲什么？")
    h.wait(qtbot)
    sent = json.dumps(server.requests[-1]["body"], ensure_ascii=False)
    assert "XYZ-42" in sent


def test_scanned_doc_without_ocr_explains(make, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    h = make()
    h.controller.close_document()
    h.controller.open_file(_blank_doc(tmp_path / "scan.pdf"))
    h.ai.reset_document()
    h.ai.ask("问题")
    assert any("OCR" in m for m in h.messages)


def test_ocr_text_dropped_when_page_count_changes(make) -> None:  # type: ignore[no-untyped-def]
    h = make()
    h.ai.add_ocr_text(0, "  ")  # 空结果忽略
    assert h.ai._ocr == {}
    h.ai.add_ocr_text(0, "ocr")
    h.ai.invalidate_text()  # 页数没变：保留
    assert h.ai._ocr == {0: "ocr"}
    h.controller.delete_page(0)
    h.ai.invalidate_text()
    assert h.ai._ocr == {}
    assert "ocr" not in "".join(h.ai._page_texts())


def test_main_window_feeds_ocr_pages_to_ai(window, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    window.open_path(_blank_doc(tmp_path / "scan.pdf"))
    window._on_ocr_page_done(SimpleNamespace(page_num=1, text="识别文字"))
    assert window._ai._page_texts()[1] == "识别文字"
    window._reset_document_session()
    assert window._ai._ocr == {}


# ---------------------------------------------------------------- 速读 / TL;DR
def test_tldr_uses_overview(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("提出注意力机制 [p.1]。")
    h = make(server=server)
    h.panel.actionRequested.emit("tldr")
    h.wait(qtbot)
    assert "TL;DR" in server.last_user() and "Section 3" in server.last_user()
    assert h.panel.turns[-1].title == "TL;DR 速览"
    assert h.panel.turns[-1].answer.startswith("提出注意力机制")


def test_speed_read_marks_page_then_saves_highlights(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("2")
    h = make(server=server)
    h.panel.actionRequested.emit("speedread")
    h.wait(qtbot)
    sent = server.last_user()
    assert "[1] Section 1 attention mechanism" in sent
    assert "[2] Body text of page 1 about transformers." in sent

    turn = h.panel.turns[-1]
    assert turn.title == "速读高亮 · 第 1 页" and turn.quote == ""
    assert "标出 1 句要点" in turn.answer and "Body text of page 1" in turn.answer
    assert list(h.marks) == [0] and len(h.marks[0]) == 1
    box = h.marks[0][0]
    assert box.y0 < 118 < box.y1  # 落在第二行文字上（基线 y=120）

    h.ai.save_note(turn.answer)
    annots = h.controller.list_annotations(0)
    assert [a.annot_type for a in annots] == [AnnotationType.HIGHLIGHT]
    assert "transformers" in annots[0].marked_text
    assert h.marks == {} and h.added == [0]

    # 已存过：再点「存为批注」退回普通便签，不会重复加高亮
    h.ai.save_note(turn.answer)
    types = [a.annot_type for a in h.controller.list_annotations(0)]
    assert types == [AnnotationType.HIGHLIGHT, AnnotationType.TEXT]


def test_speed_read_unparseable_answer_and_clear(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("我觉得都很重要", "1, 2")
    h = make(server=server)
    h.ai.speed_read(1)
    h.wait(qtbot)
    assert "没能从回答里取到句子编号" in h.panel.turns[-1].answer
    assert h.marks == {}

    h.ai.speed_read(1)
    h.wait(qtbot)
    assert len(h.marks[1]) == 2
    h.ai.clear()
    assert h.marks == {}


def test_speed_read_page_without_text(make, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    h = make()
    h.controller.close_document()
    h.ai.speed_read()
    assert h.messages[-1] == "请先打开文档"
    h.controller.open_file(_blank_doc(tmp_path / "scan.pdf"))
    h.ai.speed_read()
    assert "没有可定位的文字" in h.messages[-1]
    assert h.ai.workers() == []


def test_main_window_speed_read_marks_view(window, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("1")
    window._ai._client_factory = server.factory
    window._ai.apply_settings(_configured())
    window._ai.confirm = lambda *a, **k: (True, False)  # type: ignore[method-assign]
    window.open_path(_make_doc(tmp_path / "w.pdf"))
    assert window.ai_speedread_action.isEnabled() and window.ai_tldr_action.isEnabled()
    window.ai_speedread_action.trigger()
    qtbot.waitUntil(lambda: bool(window.pdf_view.marks()), timeout=5000)
    assert list(window.pdf_view.marks()) == [0]
    window.rotate_action.trigger()  # 页结构变化 → 标记清空
    assert window.pdf_view.marks() == {}


def test_sliced_index_avoids_main_thread_extraction(
    window: MainWindow, tmp_path: Path, qtbot, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "index.pdf"))
    qtbot.waitUntil(lambda: window._ai._texts is not None, timeout=10000)
    texts = window._ai._texts
    assert texts is not None and "Section 1" in texts[0]

    def fail() -> list[str]:
        raise AssertionError("索引就绪时不应再在主线程整篇取词")

    monkeypatch.setattr(window._controller, "page_texts", fail)
    assert window._ai._page_texts()[1] == texts[1]


def test_stop_index_cancels_pending_schedule(window: MainWindow, tmp_path: Path, qtbot) -> None:  # type: ignore[no-untyped-def]
    window.open_path(_make_doc(tmp_path / "pending.pdf"))
    window._ai.stop_index()  # 模拟标签被回收：已排队的 500 ms 定时器不得再启动索引
    qtbot.wait(700)
    assert not window._ai._index_timer.isActive()
    assert window._ai._texts is None
