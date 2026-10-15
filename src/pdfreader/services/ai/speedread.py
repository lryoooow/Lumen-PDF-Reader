"""速读高亮：把一页的词切成句子、编号交给模型挑重点，再把挑中的句子映射回页面矩形。

模型只回编号，不回原文：既省 token，又不会因为模型改写句子而对不上位置。
句子按词切分（PyMuPDF 的词是最小定位单位），所以矩形总能精确落在原文上。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pdfreader.engines.types import Rect, WordBox
from pdfreader.utils.text import join_words

__all__ = ["MAX_SENTENCES", "Sentence", "pick_count", "split_sentences"]

# 一页最多编号这么多句（极密的页只看前面部分，避免一次发太多）
MAX_SENTENCES = 150
# 少于这么多字符的「句子」（页眉、页码、公式碎片）不参与挑选
_MIN_CHARS = 12
_ENDINGS = (".", "?", "!", "。", "？", "！")
_CLOSERS = "\"'”’)]）】」』"
# 以句点结尾但通常不是句末的缩写
_ABBREVIATIONS = frozenset(
    {"e.g.", "i.e.", "al.", "fig.", "figs.", "eq.", "eqs.", "vs.", "etc.", "cf.", "sec.", "tab."}
    | {"no.", "dr.", "mr.", "ms.", "prof.", "ref.", "refs.", "approx.", "resp.", "ch."}
)


@dataclass(frozen=True)
class Sentence:
    """一句原文及其所在的行矩形（可见帧坐标，每行一个）。"""

    text: str
    rects: tuple[Rect, ...]


def _ends_sentence(word: str) -> bool:
    token = word.rstrip(_CLOSERS)
    if not token.endswith(_ENDINGS):
        return False
    if token.lower() in _ABBREVIATIONS:
        return False
    # 姓名首字母（「A.」）与编号（「1.」「2.3.」）不算句末
    core = token.rstrip(".")
    return not (core and len(core) <= 3 and (core.isupper() or core.replace(".", "").isdigit()))


def _line_rects(words: Sequence[WordBox]) -> tuple[Rect, ...]:
    lines: dict[tuple[int, int], list[Rect]] = {}
    for w in words:
        lines.setdefault((w.block, w.line), []).append(w.rect)
    return tuple(
        Rect(
            min(r.x0 for r in rects),
            min(r.y0 for r in rects),
            max(r.x1 for r in rects),
            max(r.y1 for r in rects),
        )
        for rects in lines.values()
    )


def split_sentences(words: Sequence[WordBox]) -> list[Sentence]:
    """按阅读顺序把词切成句子：句末标点或换块（段落 / 栏）处断开。"""
    sentences: list[Sentence] = []
    current: list[WordBox] = []

    def flush() -> None:
        if not current:
            return
        text = ""
        for w in current:
            text = text + join_words(text, w.text) + w.text if text else w.text
        if len(text.strip()) >= _MIN_CHARS:
            sentences.append(Sentence(text.strip(), _line_rects(current)))
        current.clear()

    block: int | None = None
    for word in words:
        if block is not None and word.block != block:
            flush()
        block = word.block
        current.append(word)
        if _ends_sentence(word.text):
            flush()
    flush()
    return sentences[:MAX_SENTENCES]


def pick_count(total: int) -> int:
    """建议挑选的句数：约六分之一，限制在 3~8 句（总数更少时全挑也不超过总数）。"""
    return min(total, max(3, min(8, total // 6)))
