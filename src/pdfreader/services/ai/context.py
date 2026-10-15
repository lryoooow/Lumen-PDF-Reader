"""文档上下文：分块、BM25 检索、``[p.N]`` 页码引用的生成与解析。

页码一律是 **1 起的物理页序**（与状态栏一致），点击引用时减 1 跳页。
检索用纯 Python 的 BM25：英文按词、中日韩按相邻二字切分，无需向量库与额外依赖。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "Bm25Index",
    "Chunk",
    "chunk_pages",
    "cited_pages",
    "link_citations",
    "overview_context",
    "question_context",
    "tokenize",
]

# 默认上下文预算（字符）：约 6k~12k token，主流模型都放得下，也不至于把整本书发出去
DEFAULT_BUDGET = 24000


@dataclass(frozen=True)
class Chunk:
    page: int  # 0 起页序
    text: str


def _clean(text: str) -> str:
    return re.sub(r"[ \t　]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def chunk_pages(pages: Sequence[str], *, size: int = 900, overlap: int = 120) -> list[Chunk]:
    """按页切块（块不跨页，保证每块都有确定页码）。"""
    chunks: list[Chunk] = []
    step = max(1, size - overlap)
    for page, raw in enumerate(pages):
        text = _clean(raw)
        if not text:
            continue
        if len(text) <= size:
            chunks.append(Chunk(page, text))
            continue
        for start in range(0, len(text), step):
            piece = text[start : start + size].strip()
            if piece:
                chunks.append(Chunk(page, piece))
            if start + size >= len(text):
                break
    return chunks


_WORD = re.compile(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]")
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "this",
        "that",
        "these",
        "those",
        "as",
        "at",
        "from",
        "it",
        "its",
        "we",
        "our",
        "their",
        "which",
        "what",
        "how",
        "why",
        "can",
    ]
)


def tokenize(text: str) -> list[str]:
    """检索用分词：英文小写词（去停用词）+ 中日韩相邻二字（单字成串时取单字）。"""
    lower = text.lower()
    tokens = [w for w in _WORD.findall(lower) if w not in _STOP]
    for run in _CJK_RUN.findall(lower):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class Bm25Index:
    """Okapi BM25（k1=1.5，b=0.75）。"""

    def __init__(self, chunks: Sequence[Chunk], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = list(chunks)
        self._k1, self._b = k1, b
        self._tf = [Counter(tokenize(c.text)) for c in self.chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self.chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        result: list[float] = []
        for tf, length in zip(self._tf, self._len, strict=True):
            score = 0.0
            norm = self._k1 * (1 - self._b + self._b * length / self._avg) if self._avg else 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    score += self._idf[term] * f * (self._k1 + 1) / (f + norm)
            result.append(score)
        return result

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        """得分最高的 k 块（得分为 0 的不要）。"""
        ranked = sorted(
            ((s, i) for i, s in enumerate(self.scores(query)) if s > 0),
            key=lambda pair: (-pair[0], pair[1]),
        )
        return [self.chunks[i] for _, i in ranked[:k]]


def _render(chunks: Sequence[Chunk]) -> str:
    return "\n\n".join(f"[p.{c.page + 1}] {c.text}" for c in chunks)


def _page_chunks(pages: Sequence[str]) -> list[Chunk]:
    return [Chunk(i, _clean(t)) for i, t in enumerate(pages) if _clean(t)]


def overview_context(pages: Sequence[str], budget: int = DEFAULT_BUDGET) -> tuple[str, bool]:
    """总结用的全文上下文 → ``(文本, 是否截取)``。

    放得下就整篇发送；放不下取开头约 2/3（摘要、引言、方法）与结尾约 1/3（结果、结论）。
    """
    whole = _page_chunks(pages)
    if sum(len(c.text) + 8 for c in whole) <= budget:
        return _render(whole), False
    head_budget, tail_budget = budget * 2 // 3, budget // 3
    head: list[Chunk] = []
    used = 0
    for chunk in whole:
        if used >= head_budget:
            break
        piece = chunk.text[: head_budget - used]
        head.append(Chunk(chunk.page, piece))
        used += len(piece) + 8
    tail: list[Chunk] = []
    used = 0
    first_unused = head[-1].page + 1 if head else 0
    for chunk in reversed(whole[first_unused:]):
        if used >= tail_budget:
            break
        piece = chunk.text[-(tail_budget - used) :]
        tail.insert(0, Chunk(chunk.page, piece))
        used += len(piece) + 8
    return _render(head) + "\n\n……（中间部分省略）……\n\n" + _render(tail), True


def question_context(
    pages: Sequence[str], question: str, budget: int = DEFAULT_BUDGET, *, k: int = 12
) -> tuple[str, bool]:
    """问答用上下文 → ``(文本, 是否经过检索截取)``。

    短文整篇发送（检索反而会漏）；长文用 BM25 取最相关的块，外加首块（标题 / 摘要），按页序排列。
    """
    whole = _page_chunks(pages)
    if sum(len(c.text) + 8 for c in whole) <= budget:
        return _render(whole), False
    chunks = chunk_pages(pages)
    picked: list[Chunk] = [chunks[0]] if chunks else []
    used = len(picked[0].text) if picked else 0
    for chunk in Bm25Index(chunks).search(question, k=max(k, 1) * 2):
        if chunk in picked:
            continue
        if used + len(chunk.text) > budget:
            continue
        picked.append(chunk)
        used += len(chunk.text) + 8
        if len(picked) > k:
            break
    order = {id(c): i for i, c in enumerate(chunks)}
    picked.sort(key=lambda c: order.get(id(c), 0))
    return _render(picked), True


# ---------------------------------------------------------------------------
# [p.N] 引用
# ---------------------------------------------------------------------------
# 兼容 [p.3] / [p. 3] / [p3] / [P.3] / [p.3, p.5] / [p.3,5] / [p.3–5]（区间取首页）
_CITE = re.compile(
    r"\[\s*[pP]\.?\s*(\d{1,5})((?:\s*[,，、;；]\s*(?:[pP]\.?\s*)?\d{1,5})*)\s*(?:[-–~]\s*\d{1,5})?\s*\]"
)
_NUM = re.compile(r"\d{1,5}")


def _cite_pages(match: re.Match[str]) -> list[int]:
    return [int(match.group(1)), *(int(n) for n in _NUM.findall(match.group(2) or ""))]


def cited_pages(text: str) -> list[int]:
    """回答里引用到的页码（1 起，去重保序）。"""
    seen: list[int] = []
    for match in _CITE.finditer(text):
        for page in _cite_pages(match):
            if page not in seen:
                seen.append(page)
    return seen


def link_citations(markdown: str, page_count: int) -> str:
    r"""把 ``[p.N]`` 改写成 Markdown 链接 ``[\[p.N\]](cite:N)``（方括号转义后仍显示）；
    超出页数的保持原样（模型编造的页码）。
    """

    def repl(match: re.Match[str]) -> str:
        pages = _cite_pages(match)
        if not all(1 <= p <= page_count for p in pages):
            return match.group(0)
        return " ".join(rf"[\[p.{p}\]](cite:{p})" for p in pages)

    return _CITE.sub(repl, markdown)
