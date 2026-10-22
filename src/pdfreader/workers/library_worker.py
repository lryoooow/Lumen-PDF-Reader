"""全文索引的 SQLite I/O；文本快照由主线程预先取好。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.services.library_files import duplicate_files
from pdfreader.services.research_library import ResearchLibrary


class LibraryIndexWorker(QThread):
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, database: Path, source: str, title: str, pages: list[str]) -> None:
        super().__init__()
        self._database = database
        self._source = source
        self._title = title
        self._pages = pages

    def run(self) -> None:
        library: ResearchLibrary | None = None
        try:
            library = ResearchLibrary(self._database)
            library.index_document(self._source, self._title, self._pages)
            self.succeeded.emit()
        except Exception:
            logging.getLogger("pdfreader").exception("建立全文索引失败")
            self.failed.emit("建立索引失败，请检查磁盘空间及目录权限。")
        finally:
            self._pages.clear()
            if library is not None:
                library.close()


class DuplicateFilesWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self._paths = paths

    def run(self) -> None:
        try:
            self.result.emit(duplicate_files(self._paths, self.isInterruptionRequested))
        except Exception:
            logging.getLogger("pdfreader").exception("重复文件扫描失败")
            self.failed.emit("重复文件扫描失败，请检查目录和权限。")
