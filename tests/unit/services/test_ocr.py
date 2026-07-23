"""OCR 服务层测试。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdfreader.services.ocr import OCR_AVAILABLE, OcrError, OcrService

SAMPLES = Path(__file__).resolve().parents[2] / "fixtures" / "samples"
SCANNED = SAMPLES / "scanned.pdf"

pytestmark = pytest.mark.skipif(not OCR_AVAILABLE, reason="rapidocr 未安装")


@pytest.fixture
def service() -> OcrService:
    return OcrService()


class TestOcrService:
    def test_ocr_scanned_page(self, service: OcrService) -> None:
        """扫描件能识别出文本。"""
        doc = pymupdf.open(SCANNED)
        pix = doc[0].get_pixmap(dpi=200)
        result = service.ocr_pixmap(pix, page_num=0)
        doc.close()
        assert result.page_num == 0
        assert "scanned" in result.text.lower() or "text" in result.text.lower()
        assert len(result.blocks) > 0

    def test_ocr_image_bytes(self, service: OcrService) -> None:
        doc = pymupdf.open(SCANNED)
        img_bytes = doc[0].get_pixmap(dpi=200).tobytes("png")
        doc.close()
        result = service.ocr_image(img_bytes, page_num=5)
        assert result.page_num == 5
        assert isinstance(result.text, str)

    def test_lazy_loading(self) -> None:
        """引擎懒加载:初始化时不创建,首次 ocr 才创建。"""
        svc = OcrService()
        assert svc._engine is None
        # 用一张小图触发加载
        doc = pymupdf.open(SCANNED)
        img_bytes = doc[0].get_pixmap(dpi=100).tobytes("png")
        doc.close()
        svc.ocr_image(img_bytes)
        assert svc._engine is not None

    def test_invalid_image_raises(self, service: OcrService) -> None:
        with pytest.raises(OcrError):
            service.ocr_image(b"not an image")
