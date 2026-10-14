"""格式转换后台线程。

导出/转换（PDF→图片/Word/Excel/TXT）是秒级到分钟级的耗时操作：`ConvertService`
内部会走 PyMuPDF / pdf2docx / pdfplumber。放在 GUI 线程执行会让界面完全冻结
（R2-P1-5），因此统一放到独立 QThread，主线程只收发结果。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.services.converter import ConvertResult, ConvertService

__all__ = ["ConvertWorker"]


class ConvertWorker(QThread):
    """在后台线程执行**一次**格式转换。

    Signals:
        convertFinished(object): 转换成功，携带 :class:`ConvertResult`
        convertFailed(str): 转换失败，携带错误消息
    """

    convertFinished = Signal(object)  # ConvertResult
    convertFailed = Signal(str)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._service = ConvertService()
        self._source: Path | None = None
        self._target: Path | None = None
        self._format: str = ""
        self._password: str | None = None

    def setup(
        self,
        source_pdf: Path,
        output_path: Path,
        target_format: str,
        password: str | None = None,
    ) -> None:
        """配置转换任务（主线程调用）。

        Args:
            source_pdf: 源 PDF
            output_path: 输出路径（不含扩展名也可，服务层会补）
            target_format: ``converter.FORMATS`` 的键
            password: 加密 PDF 的密码（快照与原文件加密方式相同，密码通用）
        """
        self._source = source_pdf
        self._target = output_path
        self._format = target_format
        self._password = password

    def run(self) -> None:
        """线程入口：执行转换并发出结果/错误。"""
        if self._source is None or self._target is None:
            self.convertFailed.emit("未指定转换任务")
            return
        try:
            result = self._service.convert(
                self._source, self._target, self._format, password=self._password
            )
        except Exception as exc:  # 服务层已把已知错误转成 ConvertError，这里是兜底
            self.convertFailed.emit(str(exc))
        else:
            if isinstance(result, ConvertResult):
                self.convertFinished.emit(result)
                return
            self.convertFailed.emit("转换返回了意外的结果")
