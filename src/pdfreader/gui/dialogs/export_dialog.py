"""导出/转换对话框。

让用户选择目标格式与输出位置,执行转换。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from pdfreader.services.converter import FORMATS, ConvertService

__all__ = ["ExportDialog"]

# PDF 可转出的格式(不含 pdf,那是图片→pdf)
EXPORT_FORMATS = ["png", "txt", "docx", "xlsx"]


class ExportDialog(QDialog):
    """导出对话框:选择格式 + 输出路径,执行转换。"""

    def __init__(self, source_pdf: Path, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._source = source_pdf
        self._service = ConvertService()
        self.setWindowTitle(self.tr("导出 / 转换"))
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # 格式选择
        fmt_layout = QHBoxLayout()
        fmt_layout.addWidget(QLabel(self.tr("目标格式:")))
        self._format_combo = QComboBox()
        for key in EXPORT_FORMATS:
            self._format_combo.addItem(FORMATS[key].name, key)
        fmt_layout.addWidget(self._format_combo)
        layout.addLayout(fmt_layout)

        # 输出路径
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel(self.tr("输出到:")))
        self._path_edit = QLineEdit()
        default_name = self._source.stem
        self._path_edit.setText(str(self._source.parent / default_name))
        path_layout.addWidget(self._path_edit)
        browse_btn = QPushButton(self.tr("浏览..."))
        browse_btn.clicked.connect(self._on_browse)
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_convert)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_browse(self) -> None:
        key = self._format_combo.currentData()
        fmt = FORMATS[key]
        path, _ = QFileDialog.getSaveFileName(self, self.tr("选择输出位置"), "", fmt.filter)
        if path:
            self._path_edit.setText(path)

    def _on_convert(self) -> None:
        key = self._format_combo.currentData()
        output = Path(self._path_edit.text().strip())
        if not output:
            QMessageBox.warning(self, self.tr("错误"), self.tr("请指定输出路径"))
            return
        try:
            result = self._service.convert(self._source, output, key)
        except Exception as exc:
            QMessageBox.critical(self, self.tr("转换失败"), str(exc))
            return
        QMessageBox.information(
            self,
            self.tr("完成"),
            self.tr("已导出到:\n%1").replace("%1", str(result.output_path)),
        )
        self.accept()
