"""OCR 服务层。

封装 RapidOCR,将扫描件(纯图片页)识别为文本。
纯 Python 实现,通过引擎层访问 PDF(渲染页面为图片再送 OCR)。

设计要点:
- 懒加载:RapidOCR 模型首次使用才初始化(避免启动慢)
- 引擎无关:接收 PixmapData 或图片字节,不直接依赖 pymupdf
- 可选依赖:rapidocr 未安装时给出清晰提示而非崩溃
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["OCR_AVAILABLE", "OcrError", "OcrResult", "OcrService"]


class OcrError(Exception):
    """OCR 相关错误。"""


try:
    from rapidocr_onnxruntime import RapidOCR

    OCR_AVAILABLE = True
except ImportError:
    RapidOCR = None
    OCR_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class OcrResult:
    """单页 OCR 结果。

    Attributes:
        page_num: 页码(0-based)
        text: 识别出的全文(各文本块按阅读顺序拼接)
        blocks: 各文本块 (text, confidence)
    """

    page_num: int
    text: str
    blocks: tuple[tuple[str, float], ...]


class OcrService:
    """OCR 服务:将页面图片识别为文本。

    懒加载 RapidOCR 引擎(首次 ocr 调用时初始化)。
    线程安全说明:RapidOCR 引擎本身可复用,但本服务设计为
    在单个 OCR 线程内使用(见 workers/ocr_worker.py)。
    """

    def __init__(self) -> None:
        self._engine: Any = None

    def _ensure_engine(self) -> None:
        """懒加载 OCR 引擎。"""
        if not OCR_AVAILABLE:
            raise OcrError(
                "OCR 功能不可用:未安装 rapidocr-onnxruntime。"
                "请运行 pip install rapidocr-onnxruntime"
            )
        if self._engine is None:
            self._engine = RapidOCR()

    def ocr_image(self, image_bytes: bytes, page_num: int = 0) -> OcrResult:
        """对一张图片做 OCR。

        Args:
            image_bytes: PNG/JPEG 图片字节
            page_num: 关联的页码(仅用于结果标记)
        """
        self._ensure_engine()
        assert self._engine is not None
        try:
            result, _elapsed = self._engine(image_bytes)
        except Exception as exc:
            raise OcrError(f"OCR 识别失败:{exc}") from exc

        if not result:
            return OcrResult(page_num=page_num, text="", blocks=())

        blocks: list[tuple[str, float]] = []
        texts: list[str] = []
        for item in result:
            # RapidOCR 每项: (box, text, confidence)
            _box, text, conf = item
            text = str(text).strip()
            if text:
                blocks.append((text, float(conf)))
                texts.append(text)

        return OcrResult(
            page_num=page_num,
            text="\n".join(texts),
            blocks=tuple(blocks),
        )

    def ocr_pixmap(self, pixmap, page_num: int = 0) -> OcrResult:  # type: ignore[no-untyped-def]
        """对 PyMuPDF Pixmap 做 OCR(转 PNG 字节后识别)。"""
        try:
            img_bytes = pixmap.tobytes("png")
        except Exception as exc:
            raise OcrError(f" pixmap 转图片失败:{exc}") from exc
        return self.ocr_image(img_bytes, page_num)
