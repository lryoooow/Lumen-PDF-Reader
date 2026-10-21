"""离线英汉词典（查词卡用）。

数据来自 ECDICT（MIT 协议，https://github.com/skywind3000/ECDICT），由
``scripts/build_dictionary.py`` 精简成约 15 万词的 SQLite，随程序发布在
``resources/dict/ecdict-lite.db``。纯标准库实现（``sqlite3``），只读打开。

查词顺序：原样 → 去掉首尾标点 → 小写 / 首字母大写 → 不规则变形表（went → go）→
规则变形（studies → study、stopped → stop、running → run、larger → large）。
查到的是变形时，卡片显示原形的释义并注明「X 是 Y 的过去式」。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Dictionary", "WordEntry", "default_dictionary", "default_dictionary_path", "is_word"]

_logger = logging.getLogger("pdfreader")

# 选中的文字是不是「一个英文单词」：字母开头，可含连字符与撇号（state-of-the-art、don't）
_WORD = re.compile(r"^[A-Za-z][A-Za-z'\-]{0,47}$")
_EDGE = "\"'“”‘’()[]{}<>.,;:!?…—–*"

_FORM_NAMES = {
    "p": "过去式",
    "d": "过去分词",
    "i": "现在分词",
    "3": "第三人称单数",
    "s": "复数",
    "r": "比较级",
    "t": "最高级",
}
_TAG_NAMES = {
    "zk": "中考",
    "gk": "高考",
    "cet4": "四级",
    "cet6": "六级",
    "ky": "考研",
    "toefl": "托福",
    "ielts": "雅思",
    "gre": "GRE",
}


def is_word(text: str) -> bool:
    """是否为单个英文单词（首尾标点不算）。"""
    return bool(_WORD.match(text.strip().strip(_EDGE)))


@dataclass(frozen=True)
class WordEntry:
    """一张词卡。

    Attributes:
        word: 词条（原形）
        phonetic: 音标（不带斜杠；可能为空）
        senses: 释义，每行一个词性（「n. 注意；注意力」「[计] 卷积的」）
        forms: 词形变化 (名称, 形式)，如 ("复数", "studies")
        tags: 考试标签的中文名（四级 / 考研 / GRE …）
        query: 用户选中的原词
        note: 变形说明（「went 是 go 的过去式」）；查的就是原形时为空
    """

    word: str
    phonetic: str
    senses: tuple[str, ...]
    forms: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()
    query: str = ""
    note: str = ""

    def summary(self, limit: int = 2) -> str:
        """一行释义摘要（存进单词本、列表里显示）。"""
        return "；".join(self.senses[:limit])


def default_dictionary_path() -> Path:
    from pdfreader import resources

    return Path(resources.__file__).resolve().parent / "dict" / "ecdict-lite.db"


class Dictionary:
    """只读词典（只在主线程查询）。文件缺失或损坏时 ``available`` 为 False，查询一律返回 None。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_dictionary_path()
        self._db: sqlite3.Connection | None = None
        self._available = self._path.is_file()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def path(self) -> Path:
        return self._path

    def _connection(self) -> sqlite3.Connection | None:
        if not self._available:
            return None
        db = self._db
        if db is None:
            try:
                db = sqlite3.connect(f"{self._path.as_uri()}?mode=ro", uri=True)
            except sqlite3.Error as exc:
                _logger.warning("词典打开失败：%s（%s）", self._path, exc)
                self._available = False
                return None
            try:
                db.execute("SELECT 1 FROM words LIMIT 1")
            except sqlite3.Error as exc:
                db.close()
                _logger.warning("词典文件损坏：%s（%s）", self._path, exc)
                self._available = False
                return None
            self._db = db
        return db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def lookup(self, text: str) -> WordEntry | None:
        """查一个词；不是单词或词典里没有返回 None。"""
        query = text.strip().strip(_EDGE)
        if not _WORD.match(query):
            return None
        db = self._connection()
        if db is None:
            return None
        try:
            return self._lookup(db, query)
        except sqlite3.Error as exc:
            _logger.warning("查词失败：%s（%s）", query, exc)
            return None

    def _lookup(self, db: sqlite3.Connection, query: str) -> WordEntry | None:
        row = self._row(db, query)
        if row is not None:
            lemma, kind = _lemma_of(row[3])
            if lemma and lemma.lower() != query.lower() and _is_inflection_only(row[2]):
                base = self._row(db, lemma)
                if base is not None:
                    return self._entry(base, query, _note(query, lemma, kind))
            return self._entry(row, query, "")
        form = db.execute(
            "SELECT lemma, kind FROM forms WHERE form = ?", (query.lower(),)
        ).fetchone()
        if form is not None:
            base = self._row(db, form[0])
            if base is not None:
                return self._entry(base, query, _note(query, form[0], form[1]))
        for candidate, kind in _guess_lemmas(query.lower()):
            base = self._row(db, candidate)
            if base is not None:
                return self._entry(base, query, _note(query, base[0], kind, guessed=True))
        return None

    @staticmethod
    def _row(db: sqlite3.Connection, word: str) -> tuple[str, str, str, str, str] | None:
        rows = db.execute(
            "SELECT word, phonetic, translation, exchange, tag FROM words WHERE key = ?",
            (word.lower(),),
        ).fetchall()
        if not rows:
            return None
        # 大小写都有时（US / us）：先要完全一致的，再要全小写的
        for wanted in (word, word.lower()):
            for r in rows:
                if r[0] == wanted:
                    return r  # type: ignore[no-any-return]
        return rows[0]  # type: ignore[no-any-return]

    @staticmethod
    def _entry(row: tuple[str, str, str, str, str], query: str, note: str) -> WordEntry:
        word, phonetic, translation, exchange, tag = row
        forms = tuple(
            (_FORM_NAMES[kind], value)
            for kind, value in _exchange_items(exchange)
            if kind in _FORM_NAMES and value.lower() != word.lower()
        )
        tags = tuple(_TAG_NAMES[t] for t in tag.split() if t in _TAG_NAMES)
        senses = tuple(line for line in translation.splitlines() if line.strip())
        return WordEntry(
            word=word,
            phonetic=phonetic.replace("ә", "ə"),  # ECDICT 里的 schwa 多是西里尔字母
            senses=senses,
            forms=_dedupe(forms),
            tags=tags,
            query=query,
            note=note,
        )


def _exchange_items(exchange: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for part in exchange.split("/"):
        kind, sep, value = part.partition(":")
        if sep and value:
            items.append((kind, value))
    return items


def _lemma_of(exchange: str) -> tuple[str, str]:
    """变形词条的 (原形, 变形类别)：ECDICT 里 ``0:`` 是原形，``1:`` 是类别（可能多个字母）。"""
    items = dict(_exchange_items(exchange))
    return items.get("0", ""), items.get("1", "")[:1]


def _is_inflection_only(translation: str) -> bool:
    """释义只是「xx 的过去式」之类的变形说明（而不是独立的词义）。"""
    return (
        "的过去" in translation
        or "的复数" in translation
        or "的现在分词" in translation
        or ("的第三人称" in translation)
    )


def _note(query: str, lemma: str, kind: str, *, guessed: bool = False) -> str:
    name = _FORM_NAMES.get(kind, "变形")
    if guessed:
        return f"{query} 可能是 {lemma} 的{name}"
    return f"{query} 是 {lemma} 的{name}"


def _guess_lemmas(word: str) -> list[tuple[str, str]]:
    """规则变形的可能原形（按常见程度排序）。"""
    out: list[tuple[str, str]] = []

    def add(stem: str, kind: str) -> None:
        if len(stem) >= 2:
            out.append((stem, kind))

    if word.endswith("ies") and len(word) > 4:
        add(word[:-3] + "y", "s")
    if word.endswith("es"):
        add(word[:-2], "s")
    if word.endswith("s") and not word.endswith("ss"):
        add(word[:-1], "s")
    if word.endswith("ied"):
        add(word[:-3] + "y", "p")
    if word.endswith("ed"):
        add(word[:-2], "p")
        add(word[:-1], "p")  # used → use
        if len(word) > 4 and word[-3] == word[-4]:
            add(word[:-3], "p")  # stopped → stop
    if word.endswith("ing"):
        add(word[:-3], "i")
        add(word[:-3] + "e", "i")  # making → make
        if len(word) > 5 and word[-4] == word[-5]:
            add(word[:-4], "i")  # running → run
    if word.endswith("ier"):
        add(word[:-3] + "y", "r")
    if word.endswith("er"):
        add(word[:-2], "r")
        add(word[:-1], "r")
    if word.endswith("iest"):
        add(word[:-4] + "y", "t")
    if word.endswith("est"):
        add(word[:-3], "t")
        add(word[:-2], "t")
    return out


def _dedupe(forms: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for item in forms:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)


_default: Dictionary | None = None


def default_dictionary() -> Dictionary:
    """程序自带的词典（进程内共用一个实例）。"""
    global _default
    if _default is None:
        _default = Dictionary()
    return _default
