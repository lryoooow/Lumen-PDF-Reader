"""OCR 后台线程。

对扫描件做 OCR 是耗时操作(每页 1-2 秒),放到独立 QThread 避免卡 UI。
与渲染/搜索线程同理:Document 在线程内,主线程通过信号收发不可变结果。
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import PdfEngineError, RenderError
from pdfreader.services.ocr import OCR_AVAILABLE, OcrError, OcrResult, OcrService

__all__ = ["OcrWorker"]

# OCR 渲染 DPI(越高越准但越慢,200 是精度/速度平衡点)
OCR_DPI = 200

# 送 OCR 的位图长边上限(像素)。200 DPI 下大幅面单页可达上亿像素，RapidOCR 直接吃
# 整页会出现内存尖峰，这里按长边限制缩放（A4 约降到 170 DPI，精度影响可忽略）。
OCR_MAX_EDGE = 2000

# 单页失败不再静默跳过,留痕便于排查（PNG 编码失败曾导致整本 OCR 无结果且无任何提示）
_logger = logging.getLogger("pdfreader")


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
    ocrCancelled = Signal(int)  # 取消：已处理页数（结果未使用）
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
        if not OCR_AVAILABLE:
            self.ocrFailed.emit("OCR 功能不可用：未安装 rapidocr-onnxruntime")
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
            if self._running:
                self.ocrCompleted.emit(processed)
            else:
                # 用户取消：必须与"完成"区分开，否则界面会弹"OCR 完成"
                self.ocrCancelled.emit(processed)
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
            pixmap = self._engine.render_page(doc, page_num, zoom=self._page_zoom(doc, page_num))
        except RenderError as exc:
            # 单页渲染失败（如页面内容损坏）：跳过该页，留日志便于排查
            _logger.warning("第 %d 页渲染失败，OCR 跳过该页：%s", page_num + 1, exc)
            return None
        # PNG 编码失败与具体页面无关，向上抛出以整体报错，避免"跑完但无结果"
        return self._ocr_pixmap_data(pixmap, page_num)

    def _ocr_pixmap_data(self, pixmap_data, page_num: int) -> OcrResult:  # type: ignore[no-untyped-def]
        """将 PixmapData 转为图片字节后 OCR（PNG 编码由引擎层负责）。"""
        img_bytes = self._engine.pixmap_to_png(pixmap_data)
        return self._ocr.ocr_image(img_bytes, page_num)

    def _page_zoom(self, doc, page_num: int) -> float:  # type: ignore[no-untyped-def]
        """该页的渲染缩放：默认 ``OCR_DPI``，但位图长边不超过 ``OCR_MAX_EDGE``。"""
        zoom = OCR_DPI / 72.0
        try:
            bounds = self._engine.page_bounds(doc, page_num)
        except PdfEngineError:
            return zoom  # 拿不到尺寸时按默认缩放
        longest = max(bounds.width, bounds.height)
        if longest > 0:
            zoom = min(zoom, OCR_MAX_EDGE / longest)
        return zoom
