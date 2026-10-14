"""OCR 结果对话框。

旧实现只在消息框里预览前 500 个字，识别结果关掉就没了。这里给出完整文本（按页分节），
可全选复制、一键复制、另存为 TXT。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

__all__ = ["OcrResultDialog", "format_ocr_text"]


def format_ocr_text(results: Mapping[int, str]) -> str:
    """按页码排序拼接识别结果，每页前加「第 N 页」标题。"""
    parts: list[str] = []
    for page_num in sorted(results):
        text = results[page_num].strip()
        parts.append(f"—— 第 {page_num + 1} 页 ——\n{text or '（未识别到文字）'}")
    return "\n\n".join(parts)


class OcrResultDialog(QDialog):
    """展示 OCR 全文，可复制 / 另存为。"""

    def __init__(
        self,
        results: Mapping[int, str],
        parent: QWidget | None = None,
        *,
        title: str = "OCR 结果",
        default_dir: Path | None = None,
        cancelled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._default_dir = default_dir or Path.home()
        self.setWindowTitle(title)
        self.resize(640, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        summary = f"已识别 {len(results)} 页" + (
            "（任务已取消，以下为取消前完成的页）" if cancelled else ""
        )
        info = QLabel(summary)
        info.setProperty("variant", "muted")
        layout.addWidget(info)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText(format_ocr_text(results))
        font = QFont(self.text_edit.font())
        font.setPointSizeF(max(font.pointSizeF(), 10.5))
        self.text_edit.setFont(font)
        layout.addWidget(self.text_edit, stretch=1)

        buttons = QHBoxLayout()
        copy_btn = QPushButton("复制全部")
        copy_btn.clicked.connect(self._copy_all)
        save_btn = QPushButton("另存为 TXT…")
        save_btn.clicked.connect(self._save_as)
        close_btn = QPushButton("关闭")
        close_btn.setProperty("variant", "primary")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(copy_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _copy_all(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self.text_edit.toPlainText())

    def _save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "保存 OCR 结果", str(self._default_dir / "OCR结果.txt"), "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            Path(path).write_text(self.text_edit.toPlainText(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"无法写入文件：{exc.strerror or exc}")
