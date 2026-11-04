"""回归审查中发现的 Qt 对象生命周期问题。"""

from threading import Event

import httpx
import pytest
from PySide6.QtWidgets import QGraphicsScene
from pytestqt.qtbot import QtBot

from pdfreader.gui.dialogs.ai_settings_dialog import AiSettingsDialog
from pdfreader.gui.widgets.pdf_view import PdfView
from pdfreader.services.ai.client import ChatClient
from pdfreader.services.ai.keystore import KeyStore
from pdfreader.services.ai.provider import AiSettings
from pdfreader.workers.ai_worker import AiWorker


@pytest.mark.parametrize("close_method", ["reject", "close"])
def test_ai_settings_waits_for_pending_request(qtbot: QtBot, close_method: str) -> None:
    entered, release = Event(), Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(8)
        return httpx.Response(200, json={"data": []})

    dialog = AiSettingsDialog(AiSettings(), KeyStore())
    qtbot.addWidget(dialog)
    worker = AiWorker(dialog)
    worker.setup_models(ChatClient("https://test.invalid", transport=httpx.MockTransport(handler)))
    dialog.show()
    dialog._start(worker, "测试中")
    try:
        assert entered.wait(3)
        getattr(dialog, close_method)()
        assert worker.isRunning()
        assert dialog.isVisible()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not dialog.isVisible(), timeout=5000)


def test_overlay_cleanup_after_scene_clear(qapp: object) -> None:
    scene = QGraphicsScene()
    items = [scene.addRect(0, 0, 10, 10)]
    scene.clear()
    PdfView._remove_overlay(items)
    assert items == []
