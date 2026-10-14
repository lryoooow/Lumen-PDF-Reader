"""签名对话框：手绘签名或选择签名图片，返回一张（透明底）PNG 路径。"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import get_theme

__all__ = ["SignatureDialog", "SignaturePad"]

_INK = "#12326e"


class SignaturePad(QWidget):
    """手绘签名画板（白底、蓝墨），可导出透明底 PNG。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(380, 150)
        self.setStyleSheet("background:#ffffff; border:1px solid #cbd3df; border-radius:8px;")
        self._strokes: list[list[QPoint]] = []
        self._cur: list[QPoint] | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._cur = [event.position().toPoint()]
        self._strokes.append(self._cur)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._cur is not None:
            self._cur.append(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._cur = None

    def _paint_strokes(self, painter: QPainter) -> None:
        pen = QPen(QColor(_INK))
        pen.setWidth(3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for stroke in self._strokes:
            for i in range(1, len(stroke)):
                painter.drawLine(stroke[i - 1], stroke[i])

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_strokes(painter)

    def clear(self) -> None:
        self._strokes = []
        self.update()

    def is_empty(self) -> bool:
        return not any(len(s) > 1 for s in self._strokes)

    def export_png(self, path: Path) -> bool:
        """导出透明底 PNG，返回是否写入成功。"""
        img = QImage(self.size(), QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_strokes(painter)
        painter.end()
        return bool(img.save(str(path)))  # 扩展名 .png 决定格式


class SignatureDialog(QDialog):
    """手绘或选择图片作为签名。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = get_theme()
        self._image_path: str | None = None
        self._is_temporary = False
        self.setWindowTitle("签名")
        self.setMinimumWidth(420)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        heading = QLabel("签名")
        heading.setProperty("variant", "heading")
        layout.addWidget(heading)
        hint = QLabel("在下方手绘签名，或选择一张签名图片。")
        hint.setProperty("variant", "muted")
        layout.addWidget(hint)

        self._pad = SignaturePad()
        pad_row = QHBoxLayout()
        pad_row.addStretch(1)
        pad_row.addWidget(self._pad)
        pad_row.addStretch(1)
        layout.addLayout(pad_row)

        tools = QHBoxLayout()
        clear_btn = QPushButton("清除")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._pad.clear)
        choose_btn = QPushButton("选择图片…")
        choose_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        choose_btn.clicked.connect(self._on_choose_image)
        tools.addWidget(clear_btn)
        tools.addWidget(choose_btn)
        tools.addStretch(1)
        layout.addLayout(tools)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setProperty("variant", "ghost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确定")
        ok.setProperty("variant", "primary")
        ok.setDefault(True)
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self._on_accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def _on_choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择签名图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)"
        )
        if path:
            self._image_path = path
            self.accept()

    def _on_accept(self) -> None:
        if self._image_path:
            self.accept()
            return
        if not self._pad.is_empty():
            # 每次一个唯一临时文件（旧实现固定名且从不清理）；由调用方放置后删除
            fd, name = tempfile.mkstemp(prefix="pdfreader_sign_", suffix=".png")
            os.close(fd)
            tmp = Path(name)
            if not self._pad.export_png(tmp):
                with contextlib.suppress(OSError):
                    tmp.unlink()
                QMessageBox.warning(self, "签名", "保存签名图片失败，请重试。")
                return
            self._image_path = str(tmp)
            self._is_temporary = True
            self.accept()
            return
        QMessageBox.information(self, "签名", "请先手绘签名或选择一张签名图片。")

    def image_path(self) -> str | None:
        return self._image_path

    @property
    def is_temporary(self) -> bool:
        """签名图片是否为本对话框生成的临时文件（放置后应删除）。"""
        return self._is_temporary
