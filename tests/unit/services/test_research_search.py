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
