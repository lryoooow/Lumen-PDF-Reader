"""只读 Zotero 库：条目 / 合集 / PDF 附件、批注解析、路径匹配与转成引擎可写入的形式。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from pdfreader.services.zotero import (
    ZoteroAnnotation,
    ZoteroCollection,
    ZoteroError,
    ZoteroLibrary,
    find_data_dir,
    to_pdf_space_annotations,
)

_SCHEMA = """
CREATE TABLE itemTypes(itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE items(
    itemID INTEGER PRIMARY KEY, itemTypeID INTEGER, key TEXT, dateAdded TEXT
);
CREATE TABLE fields(fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
CREATE TABLE creators(creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
CREATE TABLE itemCreators(
    itemCreatorID INTEGER PRIMARY KEY, itemID INTEGER, creatorID INTEGER,
    creatorTypeID INTEGER, orderIndex INTEGER
);
CREATE TABLE itemAttachments(
    itemID INTEGER PRIMARY KEY, parentItemID INTEGER, linkMode INTEGER,
    contentType TEXT, path TEXT
);
CREATE TABLE itemAnnotations(
    itemID INTEGER PRIMARY KEY, parentItemID INTEGER, type INTEGER, text TEXT,
    comment TEXT, color TEXT, position TEXT, sortIndex TEXT
);
CREATE TABLE collections(
    collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INTEGER
);
CREATE TABLE collectionItems(collectionID INTEGER, itemID INTEGER);
CREATE TABLE deletedItems(itemID INTEGER PRIMARY KEY);
CREATE TABLE deletedCollections(collectionID INTEGER PRIMARY KEY);
"""


def _make_library(folder: Path) -> Path:
    """建一个最小的 Zotero 数据目录：一篇论文、一个 PDF 附件、一条高亮批注。"""
    folder.mkdir(parents=True, exist_ok=True)
    storage = folder / "storage" / "PDFKEY" / "paper.pdf"
    storage.parent.mkdir(parents=True)
    storage.write_bytes(b"%PDF-1.4\n")
    db = sqlite3.connect(folder / "zotero.sqlite")
    try:
        db.executescript(_SCHEMA)
        db.executemany(
            "INSERT INTO itemTypes VALUES (?, ?)",
            [(2, "journalArticle"), (3, "attachment")],
        )
        db.executemany(
            "INSERT INTO items VALUES (?, ?, ?, ?)",
            [
                (1, 2, "ITEMKEY1", "2024-01-01 00:00:00"),
                (2, 3, "PDFKEY", "2024-01-02 00:00:00"),
            ],
        )
        db.executemany("INSERT INTO fields VALUES (?, ?)", [(1, "title"), (2, "date")])
        db.executemany(
            "INSERT INTO itemDataValues VALUES (?, ?)",
            [(1, "Attention Is All You Need"), (2, "2017-06-12 2017")],
        )
        db.executemany("INSERT INTO itemData VALUES (?, ?, ?)", [(1, 1, 1), (1, 2, 2)])
        db.executemany(
            "INSERT INTO creators VALUES (?, ?, ?)",
            [(1, "Ashish", "Vaswani"), (2, "Noam", "Shazeer"), (3, "Niki", "Parmar")],
        )
        db.executemany(
            "INSERT INTO itemCreators VALUES (?, ?, ?, ?, ?)",
            [(1, 1, 1, 1, 0), (2, 1, 2, 1, 1), (3, 1, 3, 1, 2)],
        )
        db.execute(
            "INSERT INTO itemAttachments VALUES (?, ?, ?, ?, ?)",
            (2, 1, 0, "application/pdf", "storage:paper.pdf"),
        )
        db.execute("INSERT INTO collections VALUES (?, ?, ?)", (10, "NLP", None))
        db.execute("INSERT INTO collections VALUES (?, ?, ?)", (11, "Survey", 10))
        db.execute("INSERT INTO collectionItems VALUES (?, ?)", (10, 1))
        db.execute(
            "INSERT INTO itemAnnotations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                100,
                2,
                1,
                "Attention is all you need",
                "关键句",
                "#ff6666",
                json.dumps({"pageIndex": 0, "rects": [[72, 120, 300, 135]], "paths": []}),
                "00001",
            ),
        )
        db.commit()
    finally:
        db.close()
    return folder


@pytest.fixture
def library(tmp_path: Path) -> ZoteroLibrary:
    return ZoteroLibrary(_make_library(tmp_path / "Zotero"))


def test_load_items_and_collections(library: ZoteroLibrary) -> None:
    collections, items = library.load()
    assert collections == [
        ZoteroCollection(10, "NLP", None),
        ZoteroCollection(11, "Survey", 10),
    ]
    assert len(items) == 1
    item = items[0]
    assert item.title == "Attention Is All You Need"
    assert item.creators == "Vaswani、Shazeer 等"
    assert item.year == "2017" and item.item_type == "journalArticle"
    assert item.collections == (10,)
    assert len(item.pdfs) == 1
    assert item.pdfs[0].exists and item.pdfs[0].path is not None
    assert item.pdfs[0].path.name == "paper.pdf"


def test_deleted_item_hidden(tmp_path: Path) -> None:
    folder = _make_library(tmp_path / "Zotero")
    db = sqlite3.connect(folder / "zotero.sqlite")
    try:
        db.execute("INSERT INTO deletedItems VALUES (1)")
        db.commit()
    finally:
        db.close()
    _, items = ZoteroLibrary(folder).load()
    assert items == []


def test_annotations_for_attachment(library: ZoteroLibrary) -> None:
    pdf = library.data_dir / "storage" / "PDFKEY" / "paper.pdf"
    annotations = library.annotations_for(pdf)
    assert len(annotations) == 1
    annot = annotations[0]
    assert annot.kind == "highlight" and annot.page_index == 0
    assert annot.rects == ((72.0, 120.0, 300.0, 135.0),)
    assert annot.text == "Attention is all you need"
    assert annot.comment == "关键句" and annot.color == "#ff6666"


def test_annotations_unknown_file_is_empty(library: ZoteroLibrary, tmp_path: Path) -> None:
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4\n")
    assert library.annotations_for(other) == []


def test_broken_annotation_rows_are_skipped(tmp_path: Path) -> None:
    folder = _make_library(tmp_path / "Zotero")
    db = sqlite3.connect(folder / "zotero.sqlite")
    try:
        rows = [
            # position 不是 JSON
            (101, 2, 5, "", "", "#000000", "not json", "00002"),
            # 未知类型
            (102, 2, 9, "", "", "#000000", '{"pageIndex": 0}', "00003"),
            # 没有区域也没有笔迹
            (103, 2, 1, "", "", "#000000", '{"pageIndex": 0, "rects": []}', "00004"),
            # 回收站里的批注
            (104, 2, 1, "", "", "#000000", '{"pageIndex": 0, "rects": [[1, 2, 3, 4]]}', "00005"),
            # 墨迹
            (
                105,
                2,
                4,
                "",
                "",
                "#333333",
                json.dumps({"pageIndex": 1, "paths": [[10, 10, 20, 20, 30, 15]]}),
                "00006",
            ),
        ]
        db.executemany("INSERT INTO itemAnnotations VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.execute("INSERT INTO deletedItems VALUES (104)")
        db.commit()
    finally:
        db.close()
    pdf = folder / "storage" / "PDFKEY" / "paper.pdf"
    annotations = ZoteroLibrary(folder).annotations_for(pdf)
    assert [a.kind for a in annotations] == ["highlight", "ink"]
    assert annotations[1].paths == ((10.0, 10.0, 20.0, 20.0, 30.0, 15.0),)


def test_missing_database_raises(tmp_path: Path) -> None:
    library = ZoteroLibrary(tmp_path / "nope")
    with pytest.raises(ZoteroError, match="找不到 Zotero 数据库"):
        library.annotations_for(tmp_path / "x.pdf")


def test_find_data_dir_from_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "custom" / "zotero-data"
    data.mkdir(parents=True)
    (data / "zotero.sqlite").write_bytes(b"")
    profiles = tmp_path / "appdata" / "Zotero" / "Zotero" / "Profiles" / "abc.default"
    profiles.mkdir(parents=True)
    (profiles / "prefs.js").write_text(
        'user_pref("extensions.zotero.dataDir", "{}");\n'
        'user_pref("extensions.zotero.useDataDir", true);\n'.format(
            str(data).replace("\\", "\\\\")
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert find_data_dir() == data


def test_find_data_dir_default_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / "Zotero").mkdir(parents=True)
    (home / "Zotero" / "zotero.sqlite").write_bytes(b"")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "home", lambda: home)
    assert find_data_dir() == home / "Zotero"


def test_to_pdf_space_annotations() -> None:
    annotations = [
        ZoteroAnnotation("highlight", 2, rects=((10.0, 20.0, 30.0, 40.0),), comment="好句"),
        ZoteroAnnotation("underline", 0, rects=((1.0, 2.0, 3.0, 4.0),), color="#FF0000"),
        ZoteroAnnotation("note", 1, rects=((5.0, 5.0, 25.0, 25.0),), text="便签内容"),
        ZoteroAnnotation("text", 1, rects=((5.0, 5.0, 90.0, 40.0),), comment="框里的字"),
        ZoteroAnnotation("ink", 0, paths=((1.0, 1.0, 2.0, 2.0),)),
        ZoteroAnnotation("text", 0, rects=((5.0, 5.0, 90.0, 40.0),)),  # 没有文字：跳过
        ZoteroAnnotation("ink", 0),  # 没有笔迹：跳过
    ]
    items, skipped = to_pdf_space_annotations(annotations)
    assert skipped == 2
    assert [item.kind for item in items] == ["highlight", "underline", "note", "text", "ink"]
    assert items[0].page_num == 2 and items[0].rects == ((10.0, 20.0, 30.0, 40.0),)
    assert items[0].comment == "好句"
    assert items[0].color == pytest.approx((1.0, 212 / 255, 0.0))
    assert items[1].color == (1.0, 0.0, 0.0)
    assert items[2].comment == "便签内容"  # 便签没有评论时用正文
    assert items[3].comment == "框里的字"
    assert items[4].paths == ((1.0, 1.0, 2.0, 2.0),)


def test_to_pdf_space_unknown_color() -> None:
    items, _ = to_pdf_space_annotations(
        [ZoteroAnnotation("highlight", 0, rects=((1.0, 2.0, 3.0, 4.0),), color="??")]
    )
    assert items[0].color == (1.0, 0.83, 0.0)
