"""段落对照翻译的数据模型与质量提示；不读取文件，不保存文献正文。"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from pdfreader.engines.types import Rect, WordBox
from pdfreader.utils.text import join_words

PROMPT_VERSION = "paragraph-v1"
_NUMBERS = re.compile(r"[−-]?\d+(?:[,.]\d+)*(?:[eE][+-]?\d+)?%?")
_REFERENCES = re.compile(r"^(?:references|bibliography|参考文献)\s*[:：]?\s*$", re.I)


@dataclass(frozen=True)
class Paragraph:
    """可见帧中的段落；ID 仅用于本次文档快照中的定位。"""

    id: str
    page: int
    rect: Rect
    text: str
    skip_reason: str = ""


def _join_line(words: Sequence[WordBox]) -> str:
    text = ""
    for word in words:
        text += join_words(text, word.text) + word.text if text else word.text
    return text


def paragraphs_from_words(page: int, words: Sequence[WordBox], bounds: Rect) -> list[Paragraph]:
    """按引擎块/行聚合，跨栏标题划分阅读带，再在带内按列排列。

    排除原因是启发式建议，界面允许用户改选。坐标直接沿用引擎的可见帧。
    """
    blocks: dict[int, dict[int, list[WordBox]]] = {}
    for word in words:
        blocks.setdefault(word.block, {}).setdefault(word.line, []).append(word)
    result: list[Paragraph] = []
    for block, lines in blocks.items():
        ordered = [sorted(line, key=lambda w: w.word_no) for _, line in sorted(lines.items())]
        text = ""
        for line in ordered:
            part = _join_line(line)
            if text.endswith("-") and part[:1].islower():
                text = text[:-1] + part
            else:
                text += join_words(text, part) + part if text else part
        all_words = [word for line in ordered for word in line]
        rect = Rect(
            min(w.rect.x0 for w in all_words),
            min(w.rect.y0 for w in all_words),
            max(w.rect.x1 for w in all_words),
            max(w.rect.y1 for w in all_words),
        )
        reason = ""
        if (
            rect.y1 <= bounds.y0 + bounds.height * 0.03
            or rect.y0 >= bounds.y1 - bounds.height * 0.03
        ):
            reason = "页眉/页脚候选"
        elif _REFERENCES.fullmatch(text.strip()):
            reason = "参考文献标题"
        elif text and sum(c.isdigit() or c in "=+∑∫√<>^_{}" for c in text) / len(text) > 0.45:
            reason = "公式候选"
        # 长块按字符上限拆分，避免单次请求无限增长；保留共同原文定位。
        for offset in range(0, len(text), 2400):
            part = text[offset : offset + 2400]
            identity = hashlib.sha256(f"{page}:{block}:{offset}:{part}".encode()).hexdigest()[:24]
            result.append(Paragraph(identity, page, rect, part, reason))

    spanning = sorted(
        (p for p in result if p.rect.width >= bounds.width * 0.65), key=lambda p: p.rect.y0
    )
    pending = [p for p in result if p not in spanning]
    ordered_result: list[Paragraph] = []
    middle = bounds.x0 + bounds.width / 2
    for divider in [*spanning, None]:
        band = [p for p in pending if divider is None or p.rect.y0 < divider.rect.y0]
        left = [p for p in band if p.rect.x1 <= middle + bounds.width * 0.04]
        right = [p for p in band if p.rect.x0 >= middle - bounds.width * 0.04]
        if left and right and len(left) + len(right) == len(band):
            ordered_result.extend(sorted(left, key=lambda p: p.rect.y0))
            ordered_result.extend(sorted(right, key=lambda p: p.rect.y0))
        else:
            ordered_result.extend(sorted(band, key=lambda p: (p.rect.y0, p.rect.x0)))
        pending = [p for p in pending if p not in band]
        if divider is not None:
            ordered_result.append(divider)
    return ordered_result


def quality_warnings(
    source: str, translated: str, glossary: Sequence[tuple[str, str]] = ()
) -> list[str]:
    """检查数字的出现次数和术语译名；提示不代表语义核验。"""

    def numbers(text: str) -> Counter[str]:
        return Counter(m.replace("−", "-").replace(",", "") for m in _NUMBERS.findall(text))

    warnings: list[str] = []
    if numbers(source) != numbers(translated):
        warnings.append("数字或出现次数有变化，请核对原文")
    for original, target in glossary:
        if original.casefold() in source.casefold() and target not in translated:
            warnings.append(f"术语译名待核对：{original} → {target}")
    return warnings


def translation_key(
    source: str,
    *,
    endpoint: str,
    model: str,
    language: str,
    glossary: Sequence[tuple[str, str]],
    temperature: float | None = None,
) -> str:
    """参数和提示词变化均使缓存失效；密钥不参与缓存键。"""
    payload = [PROMPT_VERSION, source, endpoint, model, language, sorted(glossary), temperature]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


class TranslationCache:
    """有容量上限的会话内 LRU；不写入磁盘，包括加密文档译文。"""

    def __init__(self, capacity: int = 512) -> None:
        if capacity < 1:
            raise ValueError("缓存容量必须大于零")
        self._capacity = capacity
        self._items: OrderedDict[str, str] = OrderedDict()

    def get(self, key: str) -> str | None:
        result = self._items.get(key)
        if result is not None:
            self._items.move_to_end(key)
        return result

    def put(self, key: str, text: str) -> None:
        self._items[key] = text
        self._items.move_to_end(key)
        while len(self._items) > self._capacity:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()
