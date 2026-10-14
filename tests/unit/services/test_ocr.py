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


def _ocr_pixmap_or_skip(service: OcrService, pix, page_num: int = 0):  # type: ignore[no-untyped-def]
    """调 OCR；内存不足导致 ONNXRuntime 分配失败时跳过（环境问题，不该误报失败）。

    200 DPI 的 A4 位图会让 ONNX det 模型申请上百 MB，本机可用内存不足时必然抛
    ``bad allocation``；这里改成"跳过并说明原因"，与剪贴板被占用那类环境问题同一处理。
    """
    try:
        return service.ocr_pixmap(pix, page_num=page_num)
    except OcrError as exc:
        if "bad allocation" in str(exc):
            pytest.skip("可用内存不足，ONNXRuntime 分配失败（环境问题，非缺陷）")
        raise


def _ocr_image_or_skip(service: OcrService, image_bytes: bytes, page_num: int = 0):  # type: ignore[no-untyped-def]
    try:
        return service.ocr_image(image_bytes, page_num=page_num)
    except OcrError as exc:
        if "bad allocation" in str(exc):
            pytest.skip("可用内存不足，ONNXRuntime 分配失败（环境问题，非缺陷）")
        raise


class TestOcrService:
    def test_ocr_scanned_page(self, service: OcrService) -> None:
        """扫描件能识别出文本。"""
        doc = pymupdf.open(SCANNED)
        pix = doc[0].get_pixmap(dpi=200)
        result = _ocr_pixmap_or_skip(service, pix, page_num=0)
        doc.close()
        assert result.page_num == 0
        assert "scanned" in result.text.lower() or "text" in result.text.lower()
        assert len(result.blocks) > 0

    def test_ocr_image_bytes(self, service: OcrService) -> None:
        doc = pymupdf.open(SCANNED)
        img_bytes = doc[0].get_pixmap(dpi=200).tobytes("png")
        doc.close()
        result = _ocr_image_or_skip(service, img_bytes, page_num=5)
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
