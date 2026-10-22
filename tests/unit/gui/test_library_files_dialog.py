from pathlib import Path

from pdfreader.gui.dialogs.library_files_dialog import LibraryFilesDialog
from pdfreader.workers.library_worker import DuplicateFilesWorker


def test_watch_discovers_new_files_and_duplicate_scan(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    found: list[Path] = []
    dialog = LibraryFilesDialog(found.extend)
    qtbot.addWidget(dialog)
    first = tmp_path / "a.pdf"
    first.write_bytes(b"same")
    dialog.watch(tmp_path)
    assert found == [first]
    second = tmp_path / "b.pdf"
    second.write_bytes(b"same")
    dialog.refresh()
    assert found == [first, second]
    dialog.duplicates()
    qtbot.waitUntil(lambda: dialog._worker is None, timeout=5000)
    assert "1 组重复" in dialog.status.text()
    assert second.exists()
    dialog.close()


def test_duplicate_worker_unexpected_failure_is_reported(
    qtbot, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    def fail(*args):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("pdfreader.workers.library_worker.duplicate_files", fail)
    worker = DuplicateFilesWorker([tmp_path / "a.pdf"])
    with qtbot.waitSignal(worker.failed, timeout=5000) as signal:
        worker.start()
    assert "扫描失败" in signal.args[0]
    assert worker.wait(5000)
