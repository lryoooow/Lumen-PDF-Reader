"""阅读页面排版的纯几何计算；坐标不改动文档本身。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PagePlacement:
    x: float
    y: float
    width: float
    height: float


def page_rows(count: int, *, double_page: bool, cover_alone: bool) -> list[tuple[int, ...]]:
    if not double_page:
        return [(i,) for i in range(count)]
    start = 1 if cover_alone and count else 0
    rows: list[tuple[int, ...]] = [(0,)] if start else []
    rows.extend(tuple(range(i, min(i + 2, count))) for i in range(start, count, 2))
    return rows


def layout_pages(
    sizes: Sequence[tuple[float, float]],
    *,
    gap: float,
    double_page: bool = False,
    cover_alone: bool = True,
) -> tuple[list[PagePlacement], float, float]:
    rows = page_rows(len(sizes), double_page=double_page, cover_alone=cover_alone)
    widths = [sum(sizes[i][0] for i in row) + gap * (len(row) - 1) for row in rows]
    canvas_width = max(widths, default=0.0)
    placements: list[PagePlacement] = []
    top = 0.0
    for row, width in zip(rows, widths, strict=True):
        left = (canvas_width - width) / 2
        for page in row:
            w, h = sizes[page]
            placements.append(PagePlacement(left, top, w, h))
            left += w + gap
        top += max(sizes[i][1] for i in row) + gap
    return placements, canvas_width, top
