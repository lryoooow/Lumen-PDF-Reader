"""笔记反向链接的文件 I/O，不访问文档引擎。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.services.notebook import find_backlinks


class BacklinksWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            self.result.emit(find_backlinks(self._path.parent, self._path.stem))
        except Exception:
            logging.getLogger("pdfreader").exception("反向链接读取失败")
            self.failed.emit("无法读取笔记目录，请检查路径和权限。")
