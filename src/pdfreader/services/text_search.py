"""可取消的逐页搜索数据处理，正则有超时和结果上限。"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

import regex

from pdfreader.engines.types import Rect, SearchHit, WordBox
from pdfreader.utils.text import join_words


@dataclass(frozen=True)
class SearchOptions:
    case_sensitive: bool = False
    whole_word: bool = False
    regular_expression: bool = False


class TextSearch:
    def __init__(self, query: str, options: SearchOptions) -> None:
        if not query or len(query) > 1000:
            raise ValueError("查询应为 1–1000 个字符")
        expression = query if options.regular_expression else regex.escape(query)
        if options.whole_word:
            expression = rf"(?<!\w)(?:{expression})(?!\w)"
        try:
            self._pattern = regex.compile(
                expression, 0 if options.case_sensitive else regex.IGNORECASE
            )
        except regex.error as exc:
            raise ValueError("正则表达式无效") from exc

    def page_hits(self, page: int, words: Sequence[WordBox], limit: int = 2000) -> list[SearchHit]:
        text = ""
        intervals: list[tuple[int, int, WordBox]] = []
        previous: WordBox | None = None
        for word in words:
            if previous is not None:
                text += (
                    "\n"
                    if (word.block, word.line) != (previous.block, previous.line)
                    else join_words(text, word.text)
                )
            start = len(text)
            text += word.text
            intervals.append((start, len(text), word))
            previous = word
        hits: list[SearchHit] = []
        starts = [start for start, _end, _word in intervals]
        ends = [end for _start, end, _word in intervals]
        try:
            for match in self._pattern.finditer(text, timeout=0.15):
                if match.start() == match.end():
                    continue
                first = bisect_right(ends, match.start())
                last = bisect_left(starts, match.end())
                selected = [intervals[i][2] for i in range(first, last)]
                if not selected:
                    continue
                rect = Rect(
                    min(w.rect.x0 for w in selected),
                    min(w.rect.y0 for w in selected),
                    max(w.rect.x1 for w in selected),
                    max(w.rect.y1 for w in selected),
                )
                hits.append(
                    SearchHit(page, rect, text[max(0, match.start() - 40) : match.end() + 60])
                )
                if len(hits) >= limit:
                    break
        except TimeoutError as exc:
            raise ValueError("正则匹配超时，请简化表达式") from exc
        return hits
