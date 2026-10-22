"""目录监控与哈希去重预览；不自动打开、删除或上传文献。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.services.library_files import DuplicateScan, discover_documents
from pdfreader.workers.library_worker import DuplicateFilesWorker


class LibraryFilesDialog(QDialog):
    def __init__(
        self, add_paths: Callable[[list[Path]], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("监控文献目录与重复文件检查")
        self.resize(850, 600)
        self._add_paths = add_paths
        self._directory: Path | None = None
        self._paths: list[Path] = []
        self._seen: set[Path] = set()
        self._worker: DuplicateFilesWorker | None = None
        self._closing = False
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self.refresh)
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        choose = QPushButton("选择目录并监控")
        choose.clicked.connect(self.choose)
        self.scan = QPushButton("扫描二进制重复文件")
        self.scan.clicked.connect(self.duplicates)
        for widget in (choose, self.scan):
            line.addWidget(widget)
        root.addLayout(line)
        self.files = QListWidget()
        root.addWidget(self.files)
        self.status = QLabel(
            "仅监控本窗口选择的目录；发现 PDF/EPUB 后加入文献库列表，不读取正文、不联网。"
            "哈希检查只展示重复文件，不自动删除。关闭窗口停止监控。"
        )
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    def choose(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择要监控的文献目录")
        if directory:
            self.watch(Path(directory))

    def watch(self, directory: Path) -> None:
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        self._directory = directory
        self._seen.clear()
        if not self._watcher.addPath(str(directory)):
            self.status.setText("目录监控失败，请检查路径和权限。")
            return
        self.refresh()

    def _changed(self, _directory: str) -> None:
        self._debounce.start()

    def refresh(self) -> None:
        if self._directory is None:
            return
        try:
            self._paths = discover_documents(self._directory)
        except OSError:
            self.status.setText("目录不可访问，监控已暂停；文献库记录保留。")
            return
        new = [path for path in self._paths if path not in self._seen]
        self._seen.update(new)
        if new:
            self._add_paths(new)
        self.files.clear()
        self.files.addItems([str(path) for path in self._paths])
        self.status.setText(
            f"当前目录 {len(self._paths)} 个文档，本次新增 {len(new)} 个。监控仅本窗口有效。"
        )

    def duplicates(self) -> None:
        if self._worker is not None or not self._paths:
            return
        self._worker = DuplicateFilesWorker(list(self._paths))
        self._worker.result.connect(self._result)
        self._worker.failed.connect(self.status.setText)
        self._worker.finished.connect(self._finished)
        self.scan.setEnabled(False)
        self.status.setText("正在按文件大小与 SHA-256 检查重复内容…")
        self._worker.start()

    def _result(self, result: object) -> None:
        if isinstance(result, DuplicateScan):
            self.files.clear()
            for number, group in enumerate(result.groups, 1):
                self.files.addItem(f"重复组 {number}")
                self.files.addItems([str(path) for path in group])
            self.status.setText(
                f"发现 {len(result.groups)} 组重复文件；{len(result.failed)} 个文件读取失败。"
                + ("扫描被取消，结果不完整。" if result.cancelled else "源文件未修改。")
            )

    def _finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self.scan.setEnabled(True)
        if self._closing:
            self.close()

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        self._debounce.stop()
        if self._worker is not None:
            self._worker.requestInterruption()
            event.ignore()
            return
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
