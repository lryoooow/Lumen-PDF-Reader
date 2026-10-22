"""用户主动发起的 DOI 元数据查询，只发送 DOI。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.services.bibliography import CrossrefClient
from pdfreader.services.zotero import ZoteroLibrary


class MetadataWorker(QThread):
    received = Signal(object)
    failed = Signal(str)

    def __init__(self, doi: str, client: CrossrefClient | None = None) -> None:
        super().__init__()
        self._doi = doi
        self._client = client or CrossrefClient()

    def run(self) -> None:
        try:
            self.received.emit(self._client.lookup(self._doi))
        except Exception:
            logging.getLogger("pdfreader").exception("DOI 元数据查询失败")
            self.failed.emit("DOI 查询失败，请检查 DOI、网络或稍后重试。")


class ZoteroLibraryWorker(QThread):
    received = Signal(object)
    failed = Signal(str)

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self._directory = directory

    def run(self) -> None:
        try:
            self.received.emit(ZoteroLibrary(self._directory).load())
        except Exception:
            logging.getLogger("pdfreader").exception("读取 Zotero 文献库失败")
            self.failed.emit("Zotero 文献库读取失败，请检查数据目录或版本兼容性。")
