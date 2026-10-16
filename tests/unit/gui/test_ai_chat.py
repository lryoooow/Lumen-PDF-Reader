"""AI 对话视图：气泡、折叠引用卡片、Markdown 着色与面板上的「问 AI…」引用。"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QFont, QImage

from pdfreader.gui.panels import ai_panel
from pdfreader.gui.panels.ai_chat import ChatView, ContextCard, Turn, markdown_html
from pdfreader.gui.panels.ai_panel import AiPanel
from pdfreader.gui.theme import get_theme


def _png() -> bytes:
    img = QImage(40, 20, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data().data())


def test_markdown_html_styles_citations() -> None:
    palette = get_theme().palette
    html = markdown_html("见 [p.2](cite:2) 和 **重点**", palette, QFont())
    assert 'href="cite:2"' in html
    assert palette.accent_text.lower() in html.lower()
    assert palette.accent_soft.lower() in html.lower()  # 页码是淡底小标签


def test_context_card_folds_and_closes(qtbot) -> None:  # type: ignore[no-untyped-def]
    card = ContextCard("attention " * 40, "第 2 页", closable=True)
    qtbot.addWidget(card)
    assert card.text.startswith("attention")
    assert "第 2 页" in card.info.text() and "字" in card.info.text()
    assert not card.expanded and card.full.isHidden() and not card.preview.isHidden()
    card.toggle()
    assert card.expanded and not card.full.isHidden() and card.preview.isHidden()
    assert card.close_btn is not None
    with qtbot.waitSignal(card.closed):
        card.close_btn.click()


def test_context_card_image(qtbot) -> None:  # type: ignore[no-untyped-def]
    card = ContextCard("", "第 3 页", _png())
    qtbot.addWidget(card)
    assert card.close_btn is None
    assert "框选区域" in card.info.text()
    card.toggle()
    assert not card.picture.isHidden()


def test_chat_view_bubbles(qtbot) -> None:  # type: ignore[no-untyped-def]
    view = ChatView()
    qtbot.addWidget(view)
    assert not view.empty.isHidden()
    view.add_turn(Turn("翻译成简体中文", quote="Hello world", label="第 1 页"), 3)
    first = Turn("解释这段内容")
    first.answer, first.done = "答 [p.2]", True
    view.show_turn(0, first, 3, last=False)
    view.add_turn(Turn("追问"), 3)
    assert view.empty.isHidden() and len(view.bubbles) == 2
    (user0, ai0), (user1, ai1) = view.bubbles
    assert not ai1.note_btn.isVisibleTo(ai1)  # 还没回答完，不能存
    assert user0.context is not None and user0.context.text == "Hello world"
    assert user1.context is None
    assert ai0.note_btn.isHidden()  # 「存为批注」只留给最新一条
    text = view.plain_text()
    assert "Hello world" in text and "追问" in text and "答 [p.2]" in text
    with qtbot.waitSignal(view.copyRequested) as blocker:
        ai0.copy_btn.click()
    assert blocker.args == [0]
    view.clear()
    assert view.bubbles == [] and not view.empty.isHidden()


class _Clipboard:
    def __init__(self) -> None:
        self.value = ""

    def setText(self, text: str) -> None:
        self.value = text


def test_panel_copy_flashes(qtbot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clipboard = _Clipboard()  # 系统剪贴板在 CI 上可能被占用
    monkeypatch.setattr(ai_panel.QGuiApplication, "clipboard", staticmethod(lambda: clipboard))
    panel = AiPanel()
    qtbot.addWidget(panel)
    panel.set_configured(True)
    panel.begin_turn("提问")
    panel.finish_turn("答案")
    ai = panel.chat.bubbles[-1][1]
    ai.copy_btn.click()
    assert clipboard.value == "答案"
    assert ai.copy_btn.text() == "已复制"


def test_panel_attachment(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = AiPanel()
    qtbot.addWidget(panel)
    panel.set_configured(True)
    assert panel.attachment() == ("", "")
    panel.attach_context("  self-attention  ", "第 1 页")
    assert panel.attachment() == ("self-attention", "第 1 页")
    assert "引用" in panel.input.placeholderText()
    panel.attach_context("cross-attention")  # 新引用替换旧的
    assert panel.attachment() == ("cross-attention", "")
    panel.clear_attachment()
    assert panel.attachment() == ("", "")
    assert "引用" not in panel.input.placeholderText()
    panel.attach_context("x", "第 2 页")
    panel.clear()  # 换文档时一并丢掉
    assert panel.attachment() == ("", "")
