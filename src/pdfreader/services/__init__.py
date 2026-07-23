"""服务层：重型功能（OCR、格式转换、导出）。

纯 Python 实现，通过引擎层访问 PDF 能力。
**铁律**：不直接 ``import pymupdf``。
"""

from __future__ import annotations

from pdfreader.services.converter import (
    FORMATS,
    ConvertError,
    ConvertFormat,
    ConvertResult,
    ConvertService,
)
from pdfreader.services.ocr import OcrError, OcrResult, OcrService

__all__ = [
    "FORMATS",
    "ConvertError",
    "ConvertFormat",
    "ConvertResult",
    "ConvertService",
    "OcrError",
    "OcrResult",
    "OcrService",
]
