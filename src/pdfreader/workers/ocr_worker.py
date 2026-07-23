"""OCR 后台线程。

对扫描件做 OCR 是耗时操作(每页 1-2 秒),放到独立 QThread 避免卡 UI。
与渲染/搜索线程同理:Document 在线程内,主线程通过信号收发不可变结果。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import PdfEngineError
from pdfreader.services.ocr import OcrError, OcrResult, OcrService

__all__ = ["OcrWorker"]

# OCR 渲染 DPI(越高越准但越慢,200 是精度/速度平衡点)
OCR_DPI = 200


class OcrWorker(QThread):
    """OCR 后台线程。

    Signals:
        pageOcrCompleted(OcrResult): 单页 OCR 完成
        ocrCompleted(int): 全部完成,发送已处理页数
        ocrFailed(str): 失败消息
        progress(int, int): (已完成页数, 总页数)
    """

    pageOcrCompleted = Signal(object)  # OcrResult
    ocrCompleted = Signal(int)  # processed_count
    ocrFailed = Signal(str)  # message
    progress = Signal(int, int)  # done, total

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._engine = PyMuPdfEngine()
        self._ocr = OcrService()
        self._path: Path | None = None
        self._password: str | None = None
        self._page_nums: list[int] | None = None  # None=全部页
        self._running = True

    def setup(
        self,
        path: Path,
        page_nums: list[int] | None = None,
        password: str | None = None,
    ) -> None:
        """配置 OCR 任务。

        Args:
            path: PDF 路径
            page_nums: 要 OCR 的页码列表;None 表示全部页
            password: 加密 PDF 密码
        """
        self._path = path
        self._page_nums = page_nums
        self._password = password

    def shutdown(self) -> None:
        self._running = False

    def run(self) -> None:
        path = self._path
        if path is None:
            self.ocrFailed.emit("未指定文档路径")
            return

        try:
            doc = self._engine.open(path, password=self._password)
        except PdfEngineError as exc:
            self.ocrFailed.emit(str(exc))
            return
        except Exception as exc:
            self.ocrFailed.emit(f"打开文档失败:{exc}")
            return

        try:
            total = self._engine.page_count(doc)
            page_nums = self._page_nums if self._page_nums is not None else list(range(total))
            processed = 0
            for page_num in page_nums:
                if not self._running:
                    break
                result = self._ocr_page(doc, page_num)
                if result is not None:
                    self.pageOcrCompleted.emit(result)
                processed += 1
                self.progress.emit(processed, len(page_nums))
            self.ocrCompleted.emit(processed)
        except OcrError as exc:
            self.ocrFailed.emit(str(exc))
        except Exception as exc:
            self.ocrFailed.emit(f"OCR 失败:{exc}")
        finally:
            with contextlib.suppress(Exception):
                self._engine.close(doc)

    def _ocr_page(self, doc, page_num: int) -> OcrResult | None:  # type: ignore[no-untyped-def]
        """渲染单页为图片并 OCR。"""
        try:
            pixmap = self._engine.render_page(doc, page_num, zoom=OCR_DPI / 72.0)
            # PixmapData.samples 是原始字节,需要转成图片字节
            return self._ocr_pixmap_data(pixmap, page_num)
        except (PdfEngineError, OcrError):
            return None

    def _ocr_pixmap_data(self, pixmap_data, page_num: int) -> OcrResult:  # type: ignore[no-untyped-def]
        """将 PixmapData 转为图片字节后 OCR。

        用 PyMuPDF Pixmap 重建再导出 PNG(引擎层已封装数据)。
        """
        import pymupdf

        pix = pymupdf.Pixmap(
            pixmap_data.n,
            pixmap_data.width,
            pixmap_data.height,
            pixmap_data.stride,
            pixmap_data.samples,
        )
        img_bytes = pix.tobytes("png")
        return self._ocr.ocr_image(img_bytes, page_num)
