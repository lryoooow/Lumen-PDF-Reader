from dataclasses import replace
from pathlib import Path
from zipfile import ZipFile

import pytest

from pdfreader.engines.types import Annotation, AnnotationType, Rect
from pdfreader.services.notebook import (
    annotation_note,
    export_docx,
    find_backlinks,
    linked_note_path,
    preview_markdown,
    resolve_sources,
    save_markdown,
    source_anchors,
    template,
)


def test_note_source_survives_reindex_and_reports_missing_or_ambiguous() -> None:
    original = Annotation(
        1, AnnotationType.HIGHLIGHT, Rect(1, 2, 3, 4), marked_text="证据", index=2, stable_id="a1"
    )
    text = annotation_note(original)
    anchors = source_anchors(text)
    moved = replace(original, page_num=0, index=0)
    assert resolve_sources(anchors, [moved])[anchors[0].fingerprint] == moved
    assert resolve_sources(anchors, [])[anchors[0].fingerprint] is None
    assert resolve_sources(anchors, [original, moved])[anchors[0].fingerprint] is None
    assert "证据" in text and "第 2 页" in text
    assert (
        source_anchors(annotation_note(original, "document-A"))[0].fingerprint
        != source_anchors(annotation_note(original, "document-B"))[0].fingerprint
    )


def test_templates_links_and_atomic_save(tmp_path: Path) -> None:
    text = template("论文", "期刊论文") + "[[另一篇]]"
    assert "## 方法与数据" in text
    assert "note:" in preview_markdown(text)
    assert linked_note_path(tmp_path, "另一篇").name == "另一篇.md"
    with pytest.raises(ValueError):
        linked_note_path(tmp_path, "../secret")
    target = tmp_path / "笔记.md"
    save_markdown(target, text)
    assert target.read_text("utf-8") == text
    assert source_anchors("<!-- lumen-source:{invalid} -->") == []


def test_word_export_preserves_chinese(tmp_path: Path) -> None:
    target = tmp_path / "note.docx"
    export_docx(target, "# 中文论文\n\n证据与结论")
    with ZipFile(target) as archive:
        xml = archive.read("word/document.xml").decode()
        styles = archive.read("word/styles.xml").decode()
    assert "中文论文" in xml and "证据与结论" in xml
    assert 'w:eastAsia="Microsoft YaHei"' in styles


def test_backlinks_only_include_referencing_markdown(tmp_path: Path) -> None:
    (tmp_path / "当前笔记.md").write_text("[[当前笔记]]", encoding="utf-8")
    (tmp_path / "引用者.md").write_text("见 [[当前笔记]]", encoding="utf-8")
    (tmp_path / "无关.md").write_text("[[另一篇]]", encoding="utf-8")
    result = find_backlinks(tmp_path, "当前笔记")
    assert result.paths == (tmp_path / "引用者.md",) and not result.failed
