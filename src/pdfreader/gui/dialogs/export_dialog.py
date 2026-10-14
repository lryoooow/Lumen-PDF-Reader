"""导出 / 转换对话框。

让用户选择目标格式与输出位置，在后台线程执行转换。

注意「读的文件」与「显示/默认输出用的文件」是两回事：文档有未保存改动时，主窗口会把
内存内容写成临时快照交给本对话框读取，但标题与默认输出位置必须用**真实文档路径**——
旧实现把默认输出放进快照临时目录，退出时整个目录被清理，用户导出的文件随之被删。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pdfreader.gui.theme import get_theme
from pdfreader.services.converter import FORMATS, ConvertResult, ConvertService, image_targets
from pdfreader.workers import ConvertWorker

__all__ = ["ExportDialog"]

# PDF 可转出的格式（不含 pdf，那是图片→pdf）
EXPORT_FORMATS = ["png", "jpeg", "txt", "docx", "xlsx"]

# 各格式一句话说明
_FORMAT_HINT = {
    "png": "每页导出为 PNG 图片（多页自动编号）",
    "jpeg": "每页导出为 JPEG 图片（多页自动编号）",
    "txt": "提取全文为纯文本文件",
    "docx": "转换为可编辑的 Word 文档",
    "xlsx": "提取表格为 Excel 表格",
}


class ExportDialog(QDialog):
    """导出对话框：选择格式 + 输出路径，执行转换。

    Args:
        source_pdf: 实际读取的 PDF（可能是含未保存改动的临时快照）
        display_path: 用户看到的文档路径（标题与默认输出位置），默认同 ``source_pdf``
        page_count: 文档页数（用于多页图片导出的覆盖检查）；未知时传 0
        password: 加密文档的密码
    """

    def __init__(
        self,
        source_pdf: Path,
        parent: QWidget | None = None,
        *,
        display_path: Path | None = None,
        page_count: int = 0,
        password: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = source_pdf
        self._display = display_path or source_pdf
        self._page_count = page_count
        self._password = password
        self._worker: ConvertWorker | None = None
        self._theme = get_theme()
        self.setWindowTitle("导出 / 转换")
        self.setMinimumWidth(460)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        heading = QLabel("导出 / 转换")
        heading.setProperty("variant", "heading")
        layout.addWidget(heading)
        source_lbl = QLabel(f"源文件：{self._display.name}")
        source_lbl.setProperty("variant", "muted")
        layout.addWidget(source_lbl)

        form = QFormLayout()
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._format_combo = QComboBox()
        for key in EXPORT_FORMATS:
            self._format_combo.addItem(FORMATS[key].name, key)
        self._format_combo.currentIndexChanged.connect(self._on_format_changed)
        form.addRow("目标格式", self._format_combo)

        self._hint = QLabel(_FORMAT_HINT[EXPORT_FORMATS[0]])
        self._hint.setProperty("variant", "muted")
        self._hint.setWordWrap(True)
        form.addRow("", self._hint)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._path_edit = QLineEdit()
        self._path_edit.setText(str(self._display.parent / self._display.stem))
        path_row.addWidget(self._path_edit, stretch=1)
        self._browse_btn = QPushButton("浏览…")
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(self._browse_btn)
        form.addRow("输出到", path_row)

        layout.addLayout(form)

        # 转换在后台线程跑（大文档的 docx/xlsx 转换是分钟级），这里给出不确定进度
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.setProperty("variant", "ghost")
        self._cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cancel_btn.clicked.connect(self.reject)
        self._convert_btn = QPushButton("  开始转换")
        self._convert_btn.setProperty("variant", "primary")
        self._convert_btn.setIcon(
            self._theme.colored_icon("export", self._theme.palette.on_accent, size=18)
        )
        self._convert_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._convert_btn.setDefault(True)
        self._convert_btn.clicked.connect(self._on_convert)
        buttons.addWidget(self._cancel_btn)
        buttons.addWidget(self._convert_btn)
        layout.addLayout(buttons)

    def _on_format_changed(self) -> None:
        key = self._format_combo.currentData()
        self._hint.setText(_FORMAT_HINT.get(key, ""))

    def _on_browse(self) -> None:
        key = self._format_combo.currentData()
        fmt = FORMATS[key]
        path, _ = QFileDialog.getSaveFileName(
            self, "选择输出位置", self._path_edit.text(), fmt.filter
        )
        if path:
            self._path_edit.setText(path)

    # ------------------------------------------------------------------ 覆盖确认
    def _planned_outputs(self, output: Path, key: str) -> list[Path]:
        """本次转换将写出的文件（多页图片按页编号）。"""
        resolved = ConvertService.resolve_output(output, key)
        if key in ("png", "jpeg"):
            return image_targets(resolved, self._page_count)
        return [resolved]

    def _confirm_overwrite(self, outputs: list[Path]) -> bool:
        """目标已存在时询问是否覆盖（手输 / 默认路径不经过系统保存对话框的确认）。"""
        existing = [p for p in outputs if p.exists()]
        if not existing:
            return True
        names = "\n".join(p.name for p in existing[:5])
        more = f"\n…等共 {len(existing)} 个文件" if len(existing) > 5 else ""
        reply = QMessageBox.question(
            self,
            "文件已存在",
            f"以下文件已存在，是否覆盖？\n\n{names}{more}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return bool(reply == QMessageBox.StandardButton.Yes)

    # ------------------------------------------------------------------ 转换
    def _on_convert(self) -> None:
        key = self._format_combo.currentData()
        text = self._path_edit.text().strip()
        if not text:
            QMessageBox.warning(self, "错误", "请指定输出路径")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        output = Path(text)
        if not output.parent.exists():
            QMessageBox.warning(self, "错误", f"输出目录不存在：{output.parent}")
            return
        if not self._confirm_overwrite(self._planned_outputs(output, key)):
            return
        self._set_busy(True)
        worker = ConvertWorker(self)
        worker.convertFinished.connect(self._on_convert_finished)
        worker.convertFailed.connect(self._on_convert_failed)
        worker.finished.connect(self._on_worker_finished)
        worker.setup(self._source, output, key, password=self._password)
        self._worker = worker
        worker.start()

    def _on_worker_finished(self) -> None:
        """转换线程收尾：先清引用再删除，避免 closeEvent 摸到已销毁的 C++ 对象。"""
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        if isinstance(worker, ConvertWorker):
            worker.deleteLater()

    def _set_busy(self, busy: bool) -> None:
        """转换进行中：禁用输入与按钮，并在下方显示不确定进度。"""
        self._progress.setVisible(busy)
        self._convert_btn.setEnabled(not busy)
        self._cancel_btn.setEnabled(not busy)
        self._browse_btn.setEnabled(not busy)
        self._format_combo.setEnabled(not busy)
        self._path_edit.setEnabled(not busy)

    def _on_convert_finished(self, result: ConvertResult) -> None:
        self._set_busy(False)
        QMessageBox.information(self, "完成", _describe_result(result))
        self.accept()

    def _on_convert_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.critical(self, "转换失败", message)

    def _is_busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def reject(self) -> None:
        """转换进行中忽略「取消 / Esc」：QDialog 的 reject 不走 closeEvent，
        旧实现里对话框消失但后台转换继续，过一会儿又弹出「完成」。"""
        if self._is_busy():
            self._hint.setText("转换进行中，完成后自动关闭…")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """转换未结束时不允许关闭：QThread 运行中被销毁会崩。"""
        if self._is_busy():
            event.ignore()
            self._progress.setVisible(True)
            self._hint.setText("转换进行中，完成后自动关闭…")
            return
        super().closeEvent(event)


def _describe_result(result: ConvertResult) -> str:
    """成功提示：如实列出生成的文件，空结果单独说明。"""
    if result.is_empty:
        return f"未在文档中检测到表格，已生成空表格：\n{result.output_path}"
    files = result.files or (result.output_path,)
    if len(files) == 1:
        return f"已导出到：\n{files[0]}"
    return (
        f"已导出 {len(files)} 个文件到：\n{files[0].parent}\n（{files[0].name} … {files[-1].name}）"
    )
