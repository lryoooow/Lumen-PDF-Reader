"""关于对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader import __version__
from pdfreader.gui.theme import get_theme

__all__ = ["AboutDialog"]


class AboutDialog(QDialog):
    """关于本应用。"""

    diagnosticsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = get_theme()
        self.setWindowTitle("关于 Lumen")
        self.setFixedWidth(420)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(self._theme.logo_pixmap(64))
        logo.setAlignment(Qt.AlignmentFlag.AlignTop)
        top.addWidget(logo)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name = QLabel("Lumen")
        name.setProperty("variant", "heading")
        text_col.addWidget(name)
        ver = QLabel(f"版本 {__version__}")
        ver.setProperty("variant", "muted")
        text_col.addWidget(ver)
        text_col.addSpacing(8)
        desc = QLabel(
            "专注阅读，安静思考。开源的文献阅读器。\n"
            "目录与引用跳转、多色批注与笔记导出、OCR、格式转换。"
        )
        desc.setProperty("variant", "secondary")
        desc.setWordWrap(True)
        text_col.addWidget(desc)
        top.addLayout(text_col, stretch=1)
        layout.addLayout(top)

        layout.addSpacing(14)
        tech = QLabel("基于 PySide6 · PyMuPDF · RapidOCR 构建　|　AGPL-3.0 许可")
        tech.setProperty("variant", "muted")
        tech.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tech)
        credit = QLabel(
            '查词词典：<a href="https://github.com/skywind3000/ECDICT">ECDICT</a>'
            "（skywind3000，MIT 许可）"
        )
        credit.setProperty("variant", "muted")
        credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        credit.setOpenExternalLinks(True)
        layout.addWidget(credit)
        layout.addSpacing(10)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        diag = QPushButton("导出诊断信息…")
        diag.setToolTip("生成 zip：版本、运行环境、日志尾部与脱敏设置（不含文档内容）")
        diag.setCursor(Qt.CursorShape.PointingHandCursor)
        diag.clicked.connect(self.diagnosticsRequested.emit)
        buttons.addButton(diag, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self.accept)
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setProperty("variant", "primary")
        ok.setText("好的")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(buttons)
