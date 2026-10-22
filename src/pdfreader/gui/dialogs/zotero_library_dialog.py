"""只读 Zotero 文献库浏览、元数据与标准书目导出。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.dialogs.bibliography_dialog import BibliographyDialog
from pdfreader.services.bibliography import Reference
from pdfreader.services.zotero import ZoteroCollection, ZoteroItem
from pdfreader.workers.metadata_worker import ZoteroLibraryWorker


def item_reference(item: ZoteroItem) -> Reference:
    return Reference(
        item.key or str(item.id),
        item.title,
        item.author_names,
        item.year,
        item.doi,
        kind={
            "journalArticle": "article",
            "book": "book",
            "conferencePaper": "inproceedings",
            "thesis": "phdthesis",
        }.get(item.item_type, "misc"),
    )


class ZoteroLibraryDialog(QDialog):
    def __init__(
        self,
        directory: Path,
        open_pdf: Callable[[Path, Reference], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zotero 文献库（只读）")
        self.resize(850, 600)
        self._open_pdf = open_pdf
        self._items: list[ZoteroItem] = []
        self._shown: list[ZoteroItem] = []
        self._collections: list[ZoteroCollection] = []
        self._worker: ZoteroLibraryWorker | None = None
        self._closing = False
        root = QVBoxLayout(self)
        line = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("标题 / 作者 / DOI")
        self.filter.textChanged.connect(self._filter)
        self.collection = QComboBox()
        self.collection.addItem("全部合集", -1)
        self.collection.currentIndexChanged.connect(self._filter)
        line.addWidget(self.filter)
        line.addWidget(self.collection)
        root.addLayout(line)
        self.items = QListWidget()
        self.items.itemDoubleClicked.connect(self.open_selected)
        root.addWidget(self.items)
        buttons = QHBoxLayout()
        for text, action in (
            ("打开 PDF 并采用元数据", self.open_selected),
            ("在 Zotero 中选择", self.select_in_zotero),
            ("书目 / 引用 / 导出", self.bibliography),
        ):
            button = QPushButton(text)
            button.clicked.connect(action)
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.status = QLabel("正在读取本机 Zotero 数据快照，不修改数据库…")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self._worker = ZoteroLibraryWorker(directory)
        self._worker.received.connect(self._loaded)
        self._worker.failed.connect(self.status.setText)
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _loaded(self, data: object) -> None:
        if not isinstance(data, tuple) or len(data) != 2:
            self.status.setText("Zotero 数据格式无法识别。")
            return
        collections, items = data
        self._collections = [item for item in collections if isinstance(item, ZoteroCollection)]
        self._items = [item for item in items if isinstance(item, ZoteroItem)]
        for collection in self._collections:
            self.collection.addItem(collection.name, collection.id)
        self._filter()
        self.status.setText(
            f"读取到 {len(self._items)} 个有 PDF 附件的条目。批注导出使用带嵌入批注的 PDF，书目 JSON 不包含批注坐标。"
        )

    def _filter(self) -> None:
        query = self.filter.text().casefold()
        collection = self.collection.currentData()
        # 合集选择包括所有子合集，避免父合集视图漏掉子目录条目。
        ids = {collection}
        while True:
            expanded = ids | {item.id for item in self._collections if item.parent_id in ids}
            if expanded == ids:
                break
            ids = expanded
        self._shown = [
            item
            for item in self._items
            if (collection == -1 or any(c in ids for c in item.collections))
            and query in (item.title + " " + item.creators + " " + item.doi).casefold()
        ]
        self.items.clear()
        self.items.addItems(
            [
                item.title + "\n" + item.creators + " · " + item.year + " · " + item.doi
                for item in self._shown
            ]
        )
        if self._shown:
            self.items.setCurrentRow(0)

    def _selected(self) -> ZoteroItem | None:
        row = self.items.currentRow()
        return self._shown[row] if 0 <= row < len(self._shown) else None

    def open_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        pdf = next((pdf for pdf in item.pdfs if pdf.exists), None)
        if pdf is None or pdf.path is None:
            self.status.setText("PDF 附件不存在，请先在 Zotero 下载或修复链接。")
            return
        self._open_pdf(pdf.path, item_reference(item))

    def select_in_zotero(self) -> None:
        item = self._selected()
        if item is None:
            return
        if not item.select_uri:
            self.status.setText("无法确认条目所属的库，未生成跳转链接。")
            return
        if not QDesktopServices.openUrl(QUrl(item.select_uri)):
            self.status.setText("无法启动 Zotero，请检查是否安装并注册了 zotero 协议。")

    def bibliography(self) -> None:
        item = self._selected()
        if item is not None:
            dialog = BibliographyDialog(item_reference(item), self)
            dialog.exec()
            dialog.deleteLater()

    def _finished(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        if self._closing:
            self.close()

    def reject(self) -> None:
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._closing = True
        if self._worker is not None:
            event.ignore()
            self.status.setText("等待只读快照完成后关闭…")
            return
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
