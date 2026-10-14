"""文本小工具测试（中日韩判断、同行拼接、复制时合并换行）。"""

from __future__ import annotations

import pytest

from pdfreader.utils.text import clean_copied_text, has_cjk, is_cjk_char, join_words


@pytest.mark.parametrize(
    ("ch", "expected"),
    [
        ("中", True),
        ("，", True),
        ("あ", True),
        ("한", True),
        ("A", False),
        ("1", False),
        ("", False),
    ],
)
def test_is_cjk_char(ch: str, expected: bool) -> None:
    assert is_cjk_char(ch) is expected


def test_has_cjk() -> None:
    assert has_cjk("BERT模型")
    assert not has_cjk("plain text")


@pytest.mark.parametrize(
    ("left", "right", "sep"),
    [
        ("hello", "world", " "),
        ("深度", "学习", ""),
        ("使用", "BERT", ""),
        ("BERT", "模型", ""),
        ("", "x", ""),
    ],
)
def test_join_words(left: str, right: str, sep: str) -> None:
    assert join_words(left, right) == sep


class TestCleanCopiedText:
    def test_english_line_breaks_become_spaces(self) -> None:
        assert clean_copied_text("Deep learning\nhas achieved\nsuccess.") == (
            "Deep learning has achieved success."
        )

    def test_hyphenated_word_is_joined(self) -> None:
        assert clean_copied_text("inter-\nnational coop-\neration") == "international cooperation"

    def test_chinese_lines_join_without_space(self) -> None:
        assert (
            clean_copied_text("深度学习在自然\n语言处理中的应用")
            == "深度学习在自然语言处理中的应用"
        )

    def test_paragraphs_kept(self) -> None:
        text = "First line\nsecond line.\n\nNew paragraph\nhere."
        assert clean_copied_text(text) == "First line second line.\n\nNew paragraph here."

    def test_windows_newlines_and_extra_spaces(self) -> None:
        assert clean_copied_text("a  b\r\nc") == "a b c"

    def test_empty(self) -> None:
        assert clean_copied_text("") == ""
