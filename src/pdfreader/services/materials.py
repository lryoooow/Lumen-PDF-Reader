"""写作素材库：单词本、语料、引文、术语（本机 SQLite，跨文档共用）。

存在 ``~/.pdfreader/materials.db``，每条记下来源文档与页码，可以跳回原文。
纯标准库（``sqlite3``），只在主线程使用。

* 单词 ``word``：``text`` 是原形，``note`` 是释义摘要，``extra`` 里有音标；同一个词只存一条（不分大小写）。
* 语料 ``corpus``：值得借鉴的原句；``note`` 可写译文 / 心得。
* 引文 ``quote``：原句 + 可直接改写的转述（``note``）。
* 术语 ``term``：原文 → 译名（``note``），翻译时按它统一译法；同一术语只存一条。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "KINDS",
    "KIND_LABELS",
    "MAX_TERM_CHARS",
    "Material",
    "MaterialError",
    "MaterialStore",
    "default_materials_path",
    "quote_template",
]

_logger = logging.getLogger("pdfreader")

KINDS = ("word", "corpus", "quote", "term")
KIND_LABELS = {"word": "单词", "corpus": "语料", "quote": "引文", "term": "术语"}
_UNIQUE_KINDS = ("word", "term")  # 这两类同一文本只存一条（不分大小写）
MAX_TEXT_CHARS = 2000
MAX_TERM_CHARS = 80  # 术语原文上限（再长就不是术语了）
MAX_GLOSSARY = 30  # 一次翻译最多附带的术语条数


class MaterialError(Exception):
    """素材库读写失败（消息可直接给用户看）。"""


@dataclass(frozen=True)
class Material:
    id: int
    kind: str
    text: str
    note: str = ""
    doc_path: str = ""
    doc_title: str = ""
    page: int | None = None  # 0 起
    created: float = 0.0
    extra: dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def source_label(self) -> str:
        """「文档名 · 第 N 页」（没有来源时为空）。"""
        title = self.doc_title or (Path(self.doc_path).stem if self.doc_path else "")
        if not title:
            return ""
        return f"{title} · 第 {self.page + 1} 页" if self.page is not None else title


def default_materials_path() -> Path:
    return Path.home() / ".pdfreader" / "materials.db"


class MaterialStore:
    """素材库。首次写入时建库；文件损坏时抛 :class:`MaterialError`（不静默丢数据）。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_materials_path()
        self._db: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _conn(self) -> sqlite3.Connection:
        if self._db is None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self._path)
            except (OSError, sqlite3.Error) as exc:
                _logger.warning("素材库打开失败：%s（%s）", self._path, exc)
                raise MaterialError(f"素材库无法打开：{exc}") from exc
            try:
                db.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS materials(
                        id INTEGER PRIMARY KEY,
                        kind TEXT NOT NULL,
                        text TEXT NOT NULL,
                        key TEXT NOT NULL,         -- 小写文本（单词 / 术语去重、查找用）
                        note TEXT NOT NULL DEFAULT '',
                        doc_path TEXT NOT NULL DEFAULT '',
                        doc_title TEXT NOT NULL DEFAULT '',
                        page INTEGER,
                        created REAL NOT NULL,
                        extra TEXT NOT NULL DEFAULT '{}'
                    );
                    CREATE INDEX IF NOT EXISTS materials_kind_key ON materials(kind, key);
                    """
                )
            except sqlite3.Error as exc:
                db.close()
                _logger.warning("素材库文件损坏：%s（%s）", self._path, exc)
                raise MaterialError(f"素材库文件损坏：{exc}") from exc
            self._db = db
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------ 写
    def add(
        self,
        kind: str,
        text: str,
        note: str = "",
        *,
        doc_path: str = "",
        doc_title: str = "",
        page: int | None = None,
        extra: dict[str, str] | None = None,
    ) -> Material:
        """新增一条；单词 / 术语已存在时返回原有那条（术语会更新译名）。"""
        if kind not in KINDS:
            raise ValueError(f"未知素材类型：{kind}")
        text = text.strip()
        if not text:
            raise MaterialError("内容为空")
        if len(text) > MAX_TEXT_CHARS:
            raise MaterialError(f"内容太长（最多 {MAX_TEXT_CHARS} 字）")
        if kind == "term":
            if len(text) > MAX_TERM_CHARS:
                raise MaterialError(f"术语太长（最多 {MAX_TERM_CHARS} 字）")
            if not note.strip():
                raise MaterialError("请填写术语的译名")
        if kind in _UNIQUE_KINDS:
            existing = self.find(kind, text)
            if existing is not None:
                if kind == "term" and note.strip() and note.strip() != existing.note:
                    return self.update_note(existing.id, note)
                return existing
        db = self._conn()
        created = time.time()
        try:
            cur = db.execute(
                "INSERT INTO materials(kind, text, key, note, doc_path, doc_title, page, created, extra)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    text,
                    text.lower(),
                    note.strip(),
                    doc_path,
                    doc_title,
                    page,
                    created,
                    json.dumps(extra or {}, ensure_ascii=False),
                ),
            )
            db.commit()
        except sqlite3.Error as exc:
            raise MaterialError(f"保存失败：{exc}") from exc
        row_id = cur.lastrowid
        assert row_id is not None
        return Material(
            row_id, kind, text, note.strip(), doc_path, doc_title, page, created, dict(extra or {})
        )

    def remove(self, material_id: int) -> bool:
        db = self._conn()
        try:
            cur = db.execute("DELETE FROM materials WHERE id = ?", (material_id,))
            db.commit()
        except sqlite3.Error as exc:
            raise MaterialError(f"删除失败：{exc}") from exc
        return cur.rowcount > 0

    def update_note(self, material_id: int, note: str) -> Material:
        db = self._conn()
        try:
            db.execute("UPDATE materials SET note = ? WHERE id = ?", (note.strip(), material_id))
            db.commit()
        except sqlite3.Error as exc:
            raise MaterialError(f"保存失败：{exc}") from exc
        found = self.get(material_id)
        if found is None:
            raise MaterialError("这条素材已被删除")
        return found

    # ------------------------------------------------------------------ 读
    def get(self, material_id: int) -> Material | None:
        rows = self._select("WHERE id = ?", (material_id,))
        return rows[0] if rows else None

    def find(self, kind: str, text: str) -> Material | None:
        """按文本找（不分大小写）；有多条时取最新的。"""
        rows = self._select(
            "WHERE kind = ? AND key = ? ORDER BY created DESC LIMIT 1", (kind, text.strip().lower())
        )
        return rows[0] if rows else None

    def items(self, kind: str, query: str = "") -> list[Material]:
        """某类素材，最新的在前；``query`` 在原文 / 备注 / 来源里查（不分大小写）。"""
        if query.strip():
            like = f"%{_escape_like(query.strip().lower())}%"
            return self._select(
                "WHERE kind = ? AND (key LIKE ? ESCAPE '\\' OR lower(note) LIKE ? ESCAPE '\\'"
                " OR lower(doc_title) LIKE ? ESCAPE '\\') ORDER BY created DESC",
                (kind, like, like, like),
            )
        return self._select("WHERE kind = ? ORDER BY created DESC", (kind,))

    def count(self, kind: str) -> int:
        try:
            row = (
                self._conn()
                .execute("SELECT COUNT(*) FROM materials WHERE kind = ?", (kind,))
                .fetchone()
            )
        except sqlite3.Error as exc:
            raise MaterialError(f"读取失败：{exc}") from exc
        return int(row[0])

    def _select(self, where: str, params: tuple[object, ...]) -> list[Material]:
        try:
            rows = (
                self._conn()
                .execute(
                    "SELECT id, kind, text, note, doc_path, doc_title, page, created, extra"
                    f" FROM materials {where}",
                    params,
                )
                .fetchall()
            )
        except sqlite3.Error as exc:
            raise MaterialError(f"读取失败：{exc}") from exc
        return [_material(r) for r in rows]

    def glossary_for(self, text: str) -> list[tuple[str, str]]:
        """原文里出现了哪些术语：[(术语, 译名)]，长的在前（「self-attention」先于「attention」）。

        按整词匹配、不分大小写；最多 ``MAX_GLOSSARY`` 条。
        """
        if not text.strip():
            return []
        try:
            rows = (
                self._conn()
                .execute("SELECT text, note FROM materials WHERE kind = 'term' AND note != ''")
                .fetchall()
            )
        except sqlite3.Error as exc:
            raise MaterialError(f"读取失败：{exc}") from exc
        found: dict[str, tuple[str, str]] = {}
        for term, translation in sorted(rows, key=lambda r: -len(str(r[0]))):
            key = str(term).lower()
            if key in found:
                continue
            pattern = r"(?<![A-Za-z0-9])" + re.escape(str(term)) + r"(?![A-Za-z0-9])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                found[key] = (str(term), str(translation))
                if len(found) >= MAX_GLOSSARY:
                    break
        return list(found.values())

    # ------------------------------------------------------------------ 导出
    def export_markdown(self, kind: str) -> str:
        label = KIND_LABELS.get(kind, kind)
        lines = [f"# {label}", ""]
        for m in reversed(self.items(kind)):  # 导出按收藏先后
            source = f"（{m.source_label}）" if m.source_label else ""
            if kind == "word":
                phonetic = f" /{m.extra.get('phonetic', '')}/" if m.extra.get("phonetic") else ""
                lines.append(f"- **{m.text}**{phonetic}：{m.note}{source}")
            elif kind == "term":
                lines.append(f"- **{m.text}** → {m.note}{source}")
            else:
                lines.append(f"> {m.text}")
                if m.note:
                    lines.append(">")
                    lines.append(f"> {m.note}")
                if source:
                    lines.append(f"> {source}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def export_csv(self, kind: str) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["内容", "备注", "来源文档", "页码", "收藏时间"])
        for m in reversed(self.items(kind)):
            writer.writerow(
                [
                    m.text,
                    m.note,
                    m.doc_title or m.doc_path,
                    "" if m.page is None else m.page + 1,
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(m.created)),
                ]
            )
        return buf.getvalue()


def quote_template(sentence: str) -> str:
    """引文的转述起点：「Previous studies have shown that …」+ 原句（首字母按需小写）。

    只是一个可直接改写的起点（写综述时最常用的引述句式），用户可以再编辑。
    """
    text = " ".join(sentence.split()).rstrip()
    if not text:
        return ""
    first = text.split(" ", 1)[0]
    # 首词是缩写 / 专名（BERT、ImageNet、GPT-4）时保持原样，否则首字母小写
    keep = (
        first.isupper()
        or any(ch.isdigit() for ch in first)
        or any(ch.isupper() for ch in first[1:])
    )
    body = text if keep else text[0].lower() + text[1:]
    return f"Previous studies have shown that {body}"


def _material(row: tuple[object, ...]) -> Material:
    mid, kind, text, note, doc_path, doc_title, page, created, extra = row
    try:
        parsed = json.loads(str(extra))
        extras = {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except ValueError:
        extras = {}
    return Material(
        id=int(str(mid)),
        kind=str(kind),
        text=str(text),
        note=str(note),
        doc_path=str(doc_path),
        doc_title=str(doc_title),
        page=None if page is None else int(str(page)),
        created=float(str(created)),
        extra=extras,
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
