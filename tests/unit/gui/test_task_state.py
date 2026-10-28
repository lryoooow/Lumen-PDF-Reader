"""用真实信号和离线服务控制结果/线程收尾的先后次序。"""

import threading

from PySide6.QtCore import Qt

from pdfreader.gui.dialogs.research_dialog import ResearchDialog
from pdfreader.gui.dialogs.translation_dialog import TranslationDialog
from pdfreader.services.ai.client import ChatClient
from tests.unit.gui.test_ai_assistant import FakeServer
from tests.unit.gui.test_ai_assistant import make as make


def test_translation_range_change_requires_new_extraction(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("译文")
    h = make(server=server)
    dialog = TranslationDialog(h.controller, h.ai, lambda p, y: None, 0)
    qtbot.addWidget(dialog)
    dialog.extract()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    assert dialog._paragraphs
    dialog.last_page.setValue(2)
    assert not dialog._paragraphs and not dialog.start_btn.isEnabled()
    dialog.start_translation()
    assert not server.requests
    dialog.close()


def _delay_close(h, server, release):  # type: ignore[no-untyped-def]
    def factory(base: str, key: str) -> ChatClient:
        client = server.factory(base, key)
        close = client.close

        def delayed() -> None:
            release.wait(5)
            close()

        client.close = delayed  # type: ignore[method-assign]
        return client

    h.ai._client_factory = factory


def test_translation_stop_after_result_preserves_translation_and_locks_queue(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("已完成的译文")
    h = make(server=server)
    release = threading.Event()
    _delay_close(h, server, release)
    dialog = TranslationDialog(h.controller, h.ai, lambda p, y: None, 0)
    qtbot.addWidget(dialog)
    dialog.extract()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    try:
        dialog.start_translation()
        qtbot.waitUntil(lambda: "已完成的译文" in dialog.table.item(0, 2).text())
        assert dialog._worker is not None and dialog._worker.isRunning()
        assert not dialog.table.item(1, 0).flags() & Qt.ItemFlag.ItemIsEnabled
        dialog.stop()
        assert "已完成的译文" in dialog.table.item(0, 2).text()
        assert dialog._worker is not None
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
        dialog.close()
    assert len(server.requests) == 1


def test_research_result_survives_stop_during_cleanup_and_late_progress(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("结论 [d.1 p.1]「Section 1 attention mechanism」")
    h = make(server=server)
    release = threading.Event()
    _delay_close(h, server, release)
    dialog = ResearchDialog([("论文", h.controller)], h.ai, lambda d, p: None)
    qtbot.addWidget(dialog)
    dialog.prepare()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    try:
        dialog.run()
        qtbot.waitUntil(lambda: "结论" in dialog.answer.toPlainText())
        worker = dialog._worker
        assert worker is not None and worker.isRunning()
        assert not dialog.budget.isEnabled()
        dialog.stop()
        worker.progress.emit("迟到的运行进度")
        qtbot.wait(20)
        assert "迟到" not in dialog.status.text()
        assert "已生成" in dialog.status.text()
        assert "结论" in dialog.answer.toPlainText()
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
        dialog.close()


def test_research_changed_conditions_marks_old_answer_stale(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    h = make(server=FakeServer("结论 [d.1 p.1]「Section 1 attention mechanism」"))
    dialog = ResearchDialog([("论文", h.controller)], h.ai, lambda d, p: None)
    qtbot.addWidget(dialog)
    dialog.prepare()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    dialog.run()
    qtbot.waitUntil(lambda: dialog._worker is None)
    dialog.query.setText("新问题")
    assert "结论" in dialog.answer.toPlainText()
    assert "过期" in dialog.answer_state.text()
    assert not dialog._evidence and not dialog.run_btn.isEnabled()
    dialog.close()


def test_translation_last_result_during_cleanup_stays_complete_after_stop(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("最后一段已完成")
    h = make(server=server)
    release = threading.Event()
    _delay_close(h, server, release)
    dialog = TranslationDialog(h.controller, h.ai, lambda p, y: None, 0)
    qtbot.addWidget(dialog)
    dialog.extract()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    try:
        dialog.start_translation()
        qtbot.waitUntil(lambda: "最后一段已完成" in dialog.table.item(0, 2).text())
        assert not dialog._queue and dialog._worker is not None
        dialog.stop()
        assert "完成" in dialog.status.text() and "取消" not in dialog.status.text()
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
        dialog.close()
    assert "取消" not in dialog.status.text()


def test_translation_edit_during_cleanup_keeps_invalidated_state(make, qtbot) -> None:  # type: ignore[no-untyped-def]
    server = FakeServer("已产生但已过期")
    h = make(server=server)
    release = threading.Event()
    _delay_close(h, server, release)
    dialog = TranslationDialog(h.controller, h.ai, lambda p, y: None, 0)
    qtbot.addWidget(dialog)
    dialog.extract()
    qtbot.waitUntil(lambda: not dialog._timer.isActive())
    try:
        dialog.start_translation()
        qtbot.waitUntil(lambda: "已产生但已过期" in dialog.table.item(0, 2).text())
        assert h.controller.delete_page(0)
    finally:
        release.set()
        qtbot.waitUntil(lambda: dialog._worker is None)
    assert dialog.table.rowCount() == 0
    assert "文档已变化" in dialog.status.text()
    dialog.close()
