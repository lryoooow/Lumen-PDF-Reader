"""单实例转发：第二次启动把文件交给已运行的窗口。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QSignalSpy

from pdfreader.gui.single_instance import SingleInstance, default_server_name


def test_default_server_name_is_user_scoped() -> None:
    name = default_server_name()
    assert name.startswith("Lumen-PDF-Reader-")
    assert len(name) > len("Lumen-PDF-Reader-")


def test_second_instance_forwards_paths(qtbot) -> None:  # type: ignore[no-untyped-def]
    key = f"Lumen-Test-{uuid4().hex}"
    primary = SingleInstance(key)
    assert primary.try_start() is True
    try:
        second = SingleInstance(key)
        assert second.try_start() is False
        try:
            target = Path("相对 目录") / "论文.pdf"
            with qtbot.waitSignal(primary.messageReceived, timeout=3000) as blocker:
                assert second.send_open([str(target)], r"D:\文献") is True
            paths, cwd = blocker.args
            assert paths == [str(target)]
            assert cwd == r"D:\文献"
        finally:
            second.close()
    finally:
        primary.close()


def test_malformed_message_is_ignored(qtbot) -> None:  # type: ignore[no-untyped-def]
    key = f"Lumen-Test-{uuid4().hex}"
    instance = SingleInstance(key)
    with qtbot.assertNotEmitted(instance.messageReceived):
        instance._dispatch(b"not json")
        instance._dispatch(b"[1, 2, 3]")


def test_close_releases_forwarded_connections(qtbot) -> None:  # type: ignore[no-untyped-def]
    key = f"Lumen-Test-{uuid4().hex}"
    primary = SingleInstance(key)
    second = SingleInstance(key)
    assert primary.try_start()
    assert not second.try_start()
    with qtbot.waitSignal(primary.messageReceived, timeout=3000):
        assert second.send_open(["paper.pdf"], "D:/papers")
    spies = [QSignalSpy(socket.destroyed) for socket in primary.findChildren(QLocalSocket)]
    assert spies
    second.close()
    primary.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(spy.count() == 1 for spy in spies)
    assert primary._buffers == {}
    primary.close()  # 关闭可重复调用
