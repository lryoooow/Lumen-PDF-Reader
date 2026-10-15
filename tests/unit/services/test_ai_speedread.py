"""速读高亮：句子切分、建议句数与编号解析（纯逻辑，不联网）。"""

from __future__ import annotations

from pdfreader.engines.types import Rect, WordBox
from pdfreader.services.ai import prompts
from pdfreader.services.ai.speedread import MAX_SENTENCES, pick_count, split_sentences


def _words(*lines: tuple[int, int, str]) -> list[WordBox]:
    """(块, 行, 文本) → 词：每行从 x=10 起按词排开，行高 12。"""
    words: list[WordBox] = []
    for block, line, text in lines:
        x = 10.0
        y = 20.0 * (block * 10 + line)
        for n, token in enumerate(text.split()):
            words.append(WordBox(Rect(x, y, x + 8 * len(token), y + 12), token, block, line, n))
            x += 8 * len(token) + 4
    return words


def test_split_on_sentence_end_and_block() -> None:
    words = _words(
        (0, 0, "We propose a new model."),
        (0, 1, "It works well, e.g. on Fig. 3 of"),
        (0, 2, "the paper by A. Smith et al. today!"),
        (1, 0, "Heading without a full stop"),
        (2, 0, "1."),
    )
    sentences = split_sentences(words)
    assert [s.text for s in sentences] == [
        "We propose a new model.",
        "It works well, e.g. on Fig. 3 of the paper by A. Smith et al. today!",
        "Heading without a full stop",
    ]
    # 跨两行的句子给出两个行矩形，且各自覆盖该行的词
    two = sentences[1].rects
    assert len(two) == 2
    assert two[0].y0 == 20.0 and two[1].y0 == 40.0
    assert two[0].x0 == 10.0


def test_cjk_sentences_and_closers() -> None:
    words = _words(
        (0, 0, "注意力机制让模型关注重点。"), (0, 1, "实验在三个数据集上进行（见表 2）。")
    )
    texts = [s.text for s in split_sentences(words)]
    assert texts == ["注意力机制让模型关注重点。", "实验在三个数据集上进行（见表2）。"]


def test_short_fragments_dropped_and_capped() -> None:
    assert split_sentences(_words((0, 0, "p. 3"))) == []
    many = _words(*[(0, i, f"Sentence number {i} is here.") for i in range(MAX_SENTENCES + 20)])
    assert len(split_sentences(many)) == MAX_SENTENCES


def test_pick_count_bounds() -> None:
    assert pick_count(2) == 2
    assert pick_count(10) == 3
    assert pick_count(36) == 6
    assert pick_count(200) == 8


def test_parse_indices() -> None:
    assert prompts.parse_indices("3, 7, 12", 10) == [2, 6]
    assert prompts.parse_indices("[5] 和 [2]，还有 5", 5) == [1, 4]
    assert prompts.parse_indices("没有编号", 5) == []


def test_tldr_and_speedread_prompts() -> None:
    t = prompts.tldr_messages("[p.1] abc", "中文", truncated=True)
    assert "TL;DR" in t[-1]["content"] and "[p.N]" in t[-1]["content"]
    assert "开头与结尾" in t[-1]["content"]
    s = prompts.speedread_messages(["First one.", "Second one."], 2)
    assert "[1] First one.\n[2] Second one." in s[-1]["content"]
    assert "约 2 句" in s[-1]["content"]
