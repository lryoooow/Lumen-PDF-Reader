"""默认主题的活动次要文字也须可读；不把禁用文字当合规断言。"""

from __future__ import annotations

import pytest

from pdfreader.gui.theme.palette import CONTRAST, DARK, LIGHT, Palette


def _luminance(color: str) -> float:
    components = [int(color[offset : offset + 2], 16) / 255 for offset in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in components
    ]
    return sum(
        weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear, strict=True)
    )


def _contrast(foreground: str, background: str) -> float:
    first, second = sorted((_luminance(foreground), _luminance(background)))
    return (second + 0.05) / (first + 0.05)


@pytest.mark.parametrize("palette", (LIGHT, DARK, CONTRAST), ids=lambda palette: palette.name)
@pytest.mark.parametrize("background", ("window", "surface", "canvas", "popover", "card"))
def test_active_muted_text_is_readable_on_primary_surfaces(
    palette: Palette, background: str
) -> None:
    assert _contrast(palette.text_muted, getattr(palette, background)) >= 4.5


@pytest.mark.parametrize("palette", (LIGHT, DARK), ids=lambda palette: palette.name)
def test_primary_and_secondary_text_keep_visual_hierarchy(palette: Palette) -> None:
    assert (
        _contrast(palette.text, palette.surface)
        > _contrast(palette.text_secondary, palette.surface)
        > _contrast(palette.text_muted, palette.surface)
    )
