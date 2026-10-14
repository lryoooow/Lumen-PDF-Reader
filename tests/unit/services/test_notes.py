"""阅读笔记（Markdown）导出测试。"""

from __future__ import annotations

from datetime import datetime

from pdfreader.engines.types import Annotation, AnnotationType, Rect
from pdfreader.services.notes import annotations_to_markdown

NOW = datetime(2026, 10, 14, 9, 30)


def _annot(page: int, kind: int, y: float, **kw: object) -> Annotation:
    return Annotation(page_num=page, annot_type=kind, rect=Rect(10, y, 100, y + 10), **kw)  # type: ignore[arg-type]


def test_markdown_groups_by_page_in_reading_order() -> None:
    annots = [
        _annot(2, AnnotationType.TEXT, 50, content="后面再看"),
        _annot(0, AnnotationType.HIGHLIGHT, 300, marked_text="second\nquote", content="重要"),
        _annot(0, AnnotationType.UNDERLINE, 100, marked_text="first quote"),
        _annot(1, AnnotationType.INK, 10),
    ]
    md = annotations_to_markdown("Attention Is All You Need", annots, source="paper.pdf", now=NOW)
    assert md.startswith("# Attention Is All You Need\n")
    assert "- 来源：`paper.pdf`" in md
    assert "- 导出时间：2026-10-14 09:30" in md
    assert "- 批注数量：4" in md
    # 页标题按顺序出现，同页内自上而下
    assert md.index("## 第 1 页") < md.index("## 第 2 页") < md.index("## 第 3 页")
    assert md.index("> first quote") < md.index("> second")
    assert "> second\n> quote" in md
    assert "笔记：重要" in md
    assert "**手写**" in md
    assert "**便签**：后面再看" in md


def test_markdown_without_annotations() -> None:
    md = annotations_to_markdown("", [], now=NOW)
    assert md.startswith("# 阅读笔记")
    assert "暂无批注" in md
