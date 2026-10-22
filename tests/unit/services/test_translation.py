from pdfreader.engines.types import Rect, WordBox
from pdfreader.services.translation import (
    TranslationCache,
    paragraphs_from_words,
    quality_warnings,
    translation_key,
)


def word(text: str, block: int, x: float, y: float, *, width: float = 80, line: int = 0) -> WordBox:
    return WordBox(Rect(x, y, x + width, y + 12), text, block, line, 0)


def test_columns_and_spanning_heading_keep_reading_order() -> None:
    words = [
        word("heading", 0, 20, 40, width=550),
        word("right1", 1, 330, 80),
        word("left2", 2, 30, 120),
        word("left1", 3, 30, 80),
        word("right2", 4, 330, 120),
    ]
    result = paragraphs_from_words(2, words, Rect(0, 0, 600, 800))
    assert [p.text for p in result] == ["heading", "left1", "left2", "right1", "right2"]
    assert all(p.page == 2 for p in result)
    assert len({p.id for p in result}) == 5


def test_hyphen_cjk_and_skip_candidates() -> None:
    words = [
        word("atten-", 0, 30, 80),
        word("tion", 0, 30, 100, line=1),
        word("References", 1, 30, 200),
        word("123=45", 2, 30, 250),
        word("1", 3, 30, 790),
    ]
    result = paragraphs_from_words(0, words, Rect(0, 0, 600, 800))
    assert result[0].text == "attention"
    assert [p.skip_reason for p in result[1:]] == ["参考文献标题", "公式候选", "页眉/页脚候选"]
    assert paragraphs_from_words(0, [], Rect(0, 0, 600, 800)) == []


def test_numeric_counts_and_glossary() -> None:
    assert quality_warnings("12 and 12", "12")
    assert not quality_warnings("−1.5 and 1,000", "-1.5 和 1000")
    assert quality_warnings("attention 12", "注意 12", [("attention", "注意力")])
    assert not quality_warnings("attention 12", "注意力 12", [("attention", "注意力")])


def test_cache_is_bounded_and_configuration_sensitive() -> None:
    params = {
        "endpoint": "https://example.test/v1",
        "model": "m",
        "language": "中文",
        "glossary": [],
    }
    key = translation_key("source", **params)
    assert key != translation_key("source", **{**params, "model": "other"})
    assert key != translation_key("source", **{**params, "language": "英文"})
    assert key != translation_key("source", **{**params, "glossary": [("a", "b")]})
    cache = TranslationCache(2)
    cache.put(key, "译文")
    cache.put("b", "B")
    assert cache.get(key) == "译文"
    cache.put("c", "C")
    assert cache.get("b") is None
    cache.clear()
    assert cache.get(key) is None
