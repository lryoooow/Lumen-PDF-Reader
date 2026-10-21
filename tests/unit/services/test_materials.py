"""写作素材库：增删查、单词 / 术语去重、搜索、导出、损坏文件报错。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pdfreader.services.materials import (
    MAX_TERM_CHARS,
    MAX_TEXT_CHARS,
    MaterialError,
    MaterialStore,
    quote_template,
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[MaterialStore]:
    s = MaterialStore(tmp_path / "m.db")
    yield s
    s.close()


def test_add_and_list_newest_first(store: MaterialStore) -> None:
    a = store.add("corpus", "First sentence.", doc_path="C:/a.pdf", doc_title="A", page=2)
    b = store.add("corpus", "Second sentence.")
    assert [m.id for m in store.items("corpus")] == [b.id, a.id]
    assert a.source_label == "A · 第 3 页" and b.source_label == ""
    assert store.count("corpus") == 2 and store.count("word") == 0


def test_word_unique_case_insensitive(store: MaterialStore) -> None:
    w = store.add("word", "Attention", "n. 注意", extra={"phonetic": "əˈtenʃn"})
    again = store.add("word", "attention", "别的释义")
    assert again.id == w.id and store.count("word") == 1
    found = store.find("word", "ATTENTION")
    assert found is not None and found.extra == {"phonetic": "əˈtenʃn"}


def test_term_updates_translation(store: MaterialStore) -> None:
    t = store.add("term", "attention", "注意力")
    t2 = store.add("term", "Attention", "注意力机制")
    assert t2.id == t.id and t2.note == "注意力机制"


def test_remove_and_update(store: MaterialStore) -> None:
    m = store.add("quote", "Prior work showed X.", "Previous studies demonstrated that X.")
    assert store.update_note(m.id, "改写").note == "改写"
    assert store.remove(m.id) and not store.remove(m.id)
    with pytest.raises(MaterialError):
        store.update_note(m.id, "x")


def test_search(store: MaterialStore) -> None:
    store.add("corpus", "Attention is all you need.", "注意力", doc_title="Transformer")
    store.add("corpus", "Convolution is local.", doc_title="CNN 100% survey")
    assert [m.text for m in store.items("corpus", "ATTENTION")] == ["Attention is all you need."]
    assert len(store.items("corpus", "transformer")) == 1  # 来源文档名
    assert len(store.items("corpus", "100%")) == 1  # LIKE 通配符被转义
    assert store.items("corpus", "_") == []


def test_validation(store: MaterialStore) -> None:
    with pytest.raises(ValueError):
        store.add("nope", "x")
    with pytest.raises(MaterialError):
        store.add("corpus", "   ")
    with pytest.raises(MaterialError):
        store.add("corpus", "x" * (MAX_TEXT_CHARS + 1))


def test_exports(store: MaterialStore) -> None:
    store.add(
        "word", "gradient", "n. 梯度", extra={"phonetic": "ˈɡreɪdiənt"}, doc_title="P", page=0
    )
    store.add("term", "attention", "注意力")
    store.add("quote", "X holds.", "It has been shown that X holds.")
    assert "- **gradient** /ˈɡreɪdiənt/：n. 梯度（P · 第 1 页）" in store.export_markdown("word")
    assert "- **attention** → 注意力" in store.export_markdown("term")
    quote_md = store.export_markdown("quote")
    assert "> X holds." in quote_md and "> It has been shown that X holds." in quote_md
    rows = store.export_csv("word").splitlines()
    assert rows[0] == "内容,备注,来源文档,页码,收藏时间"
    assert rows[1].startswith("gradient,n. 梯度,P,1,")


def test_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "m.db"
    s1 = MaterialStore(path)
    s1.add("word", "gradient")
    s1.close()
    s2 = MaterialStore(path)
    assert s2.find("word", "gradient") is not None
    s2.close()


def test_corrupt_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "m.db"
    bad.write_bytes(b"garbage" * 200)
    s = MaterialStore(bad)
    with pytest.raises(MaterialError):
        s.add("word", "x")


def test_term_requires_translation_and_short_text(store: MaterialStore) -> None:
    with pytest.raises(MaterialError):
        store.add("term", "attention", "")
    with pytest.raises(MaterialError):
        store.add("term", "x" * (MAX_TERM_CHARS + 1), "译名")


def test_glossary_matches_whole_words_longest_first(store: MaterialStore) -> None:
    store.add("term", "attention", "注意力")
    store.add("term", "self-attention", "自注意力")
    store.add("term", "BERT", "BERT 模型")
    store.add("term", "rag", "检索增强生成")
    text = "Self-Attention extends attention; we fine-tune bert. Storage is not RAG-free."
    assert store.glossary_for(text) == [
        ("self-attention", "自注意力"),
        ("attention", "注意力"),
        ("BERT", "BERT 模型"),
        ("rag", "检索增强生成"),
    ]
    assert store.glossary_for("Storage and fragments") == []  # 不在词中间匹配
    assert store.glossary_for("") == []


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("Transformers  scale\nwell.", "Previous studies have shown that transformers scale well."),
        ("BERT improves NLU.", "Previous studies have shown that BERT improves NLU."),
        ("ImageNet is large.", "Previous studies have shown that ImageNet is large."),
        ("GPT-4 performs well.", "Previous studies have shown that GPT-4 performs well."),
        ("   ", ""),
    ],
)
def test_quote_template(sentence: str, expected: str) -> None:
    assert quote_template(sentence) == expected
