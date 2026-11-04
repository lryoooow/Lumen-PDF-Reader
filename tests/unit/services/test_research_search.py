import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from pdfreader.engines.types import Rect, WordBox
from pdfreader.services.research_library import ResearchLibrary
from pdfreader.services.text_search import SearchOptions, TextSearch


def test_search_options_unicode_and_coordinates() -> None:
    words = [
        WordBox(Rect(i * 20, 0, i * 20 + 15, 10), word, 0, 0, i)
        for i, word in enumerate(["Attention", "attention", "attentions", "注意力"])
    ]
    assert len(TextSearch("attention", SearchOptions()).page_hits(2, words)) == 3
    hits = TextSearch("attention", SearchOptions(case_sensitive=True, whole_word=True)).page_hits(
        2, words
    )
    assert len(hits) == 1 and hits[0].page_num == 2 and hits[0].rect.x0 == 20
    assert len(TextSearch("注意力", SearchOptions()).page_hits(0, words)) == 1
    assert (
        len(TextSearch("attentio[nN]", SearchOptions(regular_expression=True)).page_hits(0, words))
        == 3
    )
    with pytest.raises(ValueError, match="无效"):
        TextSearch("[", SearchOptions(regular_expression=True))


def test_regex_timeout_and_empty_matches() -> None:
    word = WordBox(Rect(0, 0, 10, 10), "a" * 100000 + "!", 0, 0, 0)
    with pytest.raises(ValueError, match="超时"):
        TextSearch("(a+)+$", SearchOptions(regular_expression=True)).page_hits(0, [word])
    assert TextSearch("^", SearchOptions(regular_expression=True)).page_hits(0, [word]) == []


@pytest.mark.parametrize(
    ("left", "right", "query"),
    [
        ("注意", "力机制", "注意力机制"),
        ("machine", "learning", "machine learning"),
    ],
)
def test_literal_search_matches_line_wrap(left: str, right: str, query: str) -> None:
    words = [
        WordBox(Rect(0, 0, 40, 10), left, 0, 0, 0),
        WordBox(Rect(0, 12, 40, 22), right, 0, 1, 0),
    ]
    assert len(TextSearch(query, SearchOptions()).page_hits(0, words)) == 1


def test_library_fulltext_updates_unicode_and_encryption(tmp_path: Path) -> None:
    library = ResearchLibrary(tmp_path / "library.db")
    first = str(tmp_path / "a.pdf")
    second = str(tmp_path / "b.pdf")
    library.index_document(first, "论文 A", ["注意力机制与测量", "unique result 42"])
    library.index_document(second, "论文 B", ["注意力机制与测量", "unique result 42"])
    assert library.search("unique")[0].page == 1
    assert len(library.search("注意力")) == 2
    assert len(library.search("机制")) == 2
    assert len(library.duplicate_texts()) == 1
    library.set_labels(first, tags=["方法", "方法"], state="在读", rating=4)
    assert library.records()[0].tags == ("方法",)
    library.index_document(first, "论文 A", ["新版本"])
    assert len(library.search("unique")) == 1
    with pytest.raises(ValueError, match="加密"):
        library.index_document(first, "秘密", ["不能落盘"], encrypted=True)
    assert library.search("不能落盘") == []
    assert library.records()[0].rating == 4
    library.close()


def test_short_library_search_centres_on_hit(tmp_path: Path) -> None:
    library = ResearchLibrary(tmp_path / "library.db")
    try:
        library.index_document(str(tmp_path / "a.pdf"), "论文", ["前" * 220 + "机制 AI"])
        assert "机制" in library.search(" 机制 ")[0].text
        assert "AI" in library.search(" AI ")[0].text
    finally:
        library.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径迁移")
def test_legacy_windows_path_migration_preserves_conflicts(tmp_path: Path) -> None:
    db = tmp_path / "library.db"
    path = str(tmp_path / "Paper.pdf")
    alias = path.swapcase()
    ResearchLibrary(db).close()
    with closing(sqlite3.connect(db)) as legacy, legacy:
        legacy.execute("PRAGMA user_version=0")
        legacy.executemany(
            "INSERT INTO documents VALUES (?,?,?,?,?,?)",
            [
                (path, "Original", "first", '["甲"]', "在读", 3),
                (alias, "Latest", "second", '["乙"]', "已读", 5),
            ],
        )
        legacy.executemany(
            "INSERT INTO pages VALUES (?,?,?)",
            [(path, 0, "original text"), (alias, 1, "latest text")],
        )
        legacy.execute("""
            CREATE TRIGGER fail_migration BEFORE DELETE ON documents
            BEGIN SELECT RAISE(ABORT, '测试迁移回滚'); END
            """)
    with pytest.raises(sqlite3.IntegrityError, match="迁移回滚"):
        ResearchLibrary(db)
    with closing(sqlite3.connect(db)) as legacy, legacy:
        assert legacy.execute("PRAGMA user_version").fetchone() == (0,)
        assert legacy.execute("SELECT COUNT(*) FROM documents").fetchone() == (2,)
        assert legacy.execute("SELECT COUNT(*) FROM pages").fetchone() == (2,)
        legacy.execute("DROP TRIGGER fail_migration")
    for _ in range(2):
        library = ResearchLibrary(db)
        try:
            (record,) = library.records()
            assert record.title == "Latest"
            assert record.rating == 5
            assert library.search("latest")[0].page == 1
            library.set_labels(path, tags=["乙"], state="已读", rating=5)
        finally:
            library.close()
    library = ResearchLibrary(db)
    try:
        library.index_document(path, "Updated", ["new indexed text"])
        library.index_document(alias, "Same document", ["replacement"])
        (record,) = library.records()
        assert record.title == "Same document"
        assert record.tags == ("乙",)
        assert library.search("replacement")[0].page == 0
    finally:
        library.close()
    with closing(sqlite3.connect(db)) as migrated:
        rows = migrated.execute("SELECT title,rating,pages FROM windows_path_history").fetchall()
        assert len(rows) == 2
        assert any(
            title == "Original" and rating == 3 and "original text" in pages
            for title, rating, pages in rows
        )
