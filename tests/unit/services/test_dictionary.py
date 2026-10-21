"""离线词典：查词顺序（原样 / 去标点 / 大小写 / 不规则变形 / 规则变形）与容错。

用的是随程序发布的精简 ECDICT；另外造一个极小的库测边界。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pdfreader.services.dictionary import Dictionary, default_dictionary, is_word


@pytest.fixture(scope="module")
def bundled() -> Iterator[Dictionary]:
    d = Dictionary()
    if not d.available:
        pytest.skip("未找到随程序发布的词典文件")
    yield d
    d.close()


@pytest.fixture
def tiny(tmp_path: Path) -> Iterator[Callable[[], Dictionary]]:
    made: list[Dictionary] = []

    def make() -> Dictionary:
        made.append(_tiny(tmp_path / "d.db"))
        return made[-1]

    yield make
    for d in made:
        d.close()


def test_default_dictionary_is_shared() -> None:
    assert default_dictionary() is default_dictionary()


def _tiny(path: Path) -> Dictionary:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE words(key, word, phonetic, translation, exchange, tag, PRIMARY KEY(key, word));
        CREATE TABLE forms(form PRIMARY KEY, lemma, kind);
        INSERT INTO words VALUES ('go', 'go', 'gəʊ', 'vi. 去', 'p:went/d:gone/i:going', 'zk cet4');
        INSERT INTO words VALUES ('us', 'US', '', 'abbr. 美国', '', '');
        INSERT INTO words VALUES ('us', 'us', 'ʌs', 'pron. 我们', '', '');
        INSERT INTO words VALUES ('studies', 'studies', '', 'n. 研究（study的复数）', '0:study/1:s3', '');
        INSERT INTO words VALUES ('study', 'study', 'ˈstʌdi', 'n. 学习
v. 研究', 's:studies/p:studied', 'cet4');
        INSERT INTO words VALUES ('stop', 'stop', 'stɒp', 'v. 停止', '', '');
        INSERT INTO forms VALUES ('went', 'go', 'p');
        """
    )
    db.commit()
    db.close()
    return Dictionary(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("attention", True),
        ("Attention,", True),
        ("state-of-the-art", True),
        ("don't", True),
        ("two words", False),
        ("注意力", False),
        ("x2", False),
        ("", False),
    ],
)
def test_is_word(text: str, expected: bool) -> None:
    assert is_word(text) is expected


class TestTinyDictionary:
    def test_direct_hit_with_forms_and_tags(self, tiny: Callable[[], Dictionary]) -> None:
        e = tiny().lookup("go")
        assert e is not None and e.word == "go" and e.phonetic == "gəʊ"
        assert ("过去式", "went") in e.forms and e.tags == ("中考", "四级")
        assert e.note == ""

    def test_punctuation_stripped(self, tiny: Callable[[], Dictionary]) -> None:
        e = tiny().lookup("“go,”")
        assert e is not None and e.word == "go"

    def test_case_preference(self, tiny: Callable[[], Dictionary]) -> None:
        d = tiny()
        us_upper, us_lower = d.lookup("US"), d.lookup("us")
        assert us_upper is not None and us_upper.senses == ("abbr. 美国",)
        assert us_lower is not None and us_lower.senses == ("pron. 我们",)

    def test_inflection_row_redirects_to_lemma(self, tiny: Callable[[], Dictionary]) -> None:
        e = tiny().lookup("studies")
        assert e is not None and e.word == "study" and e.senses == ("n. 学习", "v. 研究")
        assert e.note == "studies 是 study 的复数" and e.query == "studies"

    def test_irregular_form_table(self, tiny: Callable[[], Dictionary]) -> None:
        e = tiny().lookup("went")
        assert e is not None and e.word == "go" and e.note == "went 是 go 的过去式"

    def test_rule_guess(self, tiny: Callable[[], Dictionary]) -> None:
        e = tiny().lookup("stopping")
        assert e is not None and e.word == "stop" and "可能是 stop 的现在分词" in e.note

    def test_unknown_and_non_words(self, tiny: Callable[[], Dictionary]) -> None:
        d = tiny()
        assert d.lookup("qwertyuiop") is None
        assert d.lookup("two words") is None

    def test_missing_file(self, tmp_path: Path) -> None:
        d = Dictionary(tmp_path / "nope.db")
        assert not d.available and d.lookup("go") is None

    def test_corrupt_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"not a database" * 100)
        d = Dictionary(bad)
        assert d.lookup("go") is None and not d.available
        d.close()


class TestBundled:
    @pytest.mark.parametrize(
        ("query", "word"),
        [
            ("attention", "attention"),
            ("convolutional", "convolutional"),
            ("went", "go"),
            ("perceived", "perceive"),
            ("larger", "large"),
            ("Bayesian", "Bayesian"),
            ("(gradient)", "gradient"),
        ],
    )
    def test_common_and_technical(self, bundled: Dictionary, query: str, word: str) -> None:
        e = bundled.lookup(query)
        assert e is not None and e.word == word and e.senses

    def test_phonetic_schwa_normalized(self, bundled: Dictionary) -> None:
        e = bundled.lookup("attention")
        assert e is not None and "ә" not in e.phonetic and "ə" in e.phonetic
