"""坐标转换单元测试。"""

from __future__ import annotations

import pytest

from pdfreader.engines.types import Rect
from pdfreader.utils.coordinates import (
    PDF_DPI,
    ZOOM_PRESETS,
    ZoomLevel,
    pdf_rect_to_pixel,
    pdf_to_pixel,
    pixel_rect_to_pdf,
    pixel_to_pdf,
)


class TestPointConversion:
    def test_pdf_to_pixel_identity(self) -> None:
        assert pdf_to_pixel(100.0, zoom=1.0) == 100.0

    def test_pdf_to_pixel_2x(self) -> None:
        assert pdf_to_pixel(100.0, zoom=2.0) == 200.0

    def test_pdf_to_pixel_half(self) -> None:
        assert pdf_to_pixel(100.0, zoom=0.5) == 50.0

    def test_pixel_to_pdf_identity(self) -> None:
        assert pixel_to_pdf(100.0, zoom=1.0) == 100.0

    def test_pixel_to_pdf_2x(self) -> None:
        assert pixel_to_pdf(200.0, zoom=2.0) == 100.0

    def test_round_trip(self) -> None:
        """PDF→像素→PDF 往返应一致。"""
        original = 123.456
        assert pixel_to_pdf(pdf_to_pixel(original, 1.5), 1.5) == pytest.approx(original)

    def test_pixel_to_pdf_zero_zoom_raises(self) -> None:
        with pytest.raises(ValueError):
            pixel_to_pdf(100.0, zoom=0.0)


class TestRectConversion:
    def test_pdf_rect_to_pixel(self) -> None:
        r = Rect(10, 20, 30, 40)
        result = pdf_rect_to_pixel(r, zoom=2.0)
        assert result == Rect(20, 40, 60, 80)

    def test_pixel_rect_to_pdf(self) -> None:
        r = Rect(20, 40, 60, 80)
        result = pixel_rect_to_pdf(r, zoom=2.0)
        assert result == Rect(10, 20, 30, 40)

    def test_rect_round_trip(self) -> None:
        original = Rect(1, 2, 3, 4)
        result = pixel_rect_to_pdf(pdf_rect_to_pixel(original, 1.5), 1.5)
        assert result == pytest.approx(Rect(1, 2, 3, 4), abs=0.001) or result == Rect(1, 2, 3, 4)

    def test_pixel_rect_zero_zoom_raises(self) -> None:
        with pytest.raises(ValueError):
            pixel_rect_to_pdf(Rect(0, 0, 1, 1), zoom=0.0)


class TestZoomPresets:
    def test_presets_exist(self) -> None:
        assert len(ZOOM_PRESETS) > 0

    def test_preset_structure(self) -> None:
        for p in ZOOM_PRESETS:
            assert isinstance(p, ZoomLevel)
            assert isinstance(p.label, str)
            assert isinstance(p.zoom, float)

    def test_has_100_percent(self) -> None:
        assert any(p.zoom == 1.0 for p in ZOOM_PRESETS)

    def test_fit_width_is_special(self) -> None:
        """适应宽度用负值标记，由视图层处理。"""
        fit = next(p for p in ZOOM_PRESETS if p.label == "适应宽度")
        assert fit.zoom < 0


def test_pdf_dpi_constant() -> None:
    assert PDF_DPI == 72.0
