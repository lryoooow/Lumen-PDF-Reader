"""用户主动建立的本地全文索引与文献标记；事务更新，不索引加密文档明文。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LibraryHit:
    path: str
    page: int
    text: str
    title: str


@dataclass(frozen=True)
class LibraryRecord:
    path: str
    title: str
    tags: tuple[str, ...] = ()
    state: str = "未读"
    rating: int = 0


class ResearchLibrary:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                path TEXT PRIMARY KEY, title TEXT NOT NULL, digest TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT '未读',
                rating INTEGER NOT NULL DEFAULT 0);
            CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
                path UNINDEXED, page UNINDEXED, text, tokenize='trigram');
        """)

    def close(self) -> None:
        self._db.close()

    def index_document(
        self,
        path: str,
        title: str,
        pages: Sequence[str],
        *,
        encrypted: bool = False,
    ) -> str:
        """以当前内存快照文本更新整篇索引；调用方必须显式征得本地明文索引同意。

        digest 用于识别相同文字内容候选，不声称是 PDF 二进制去重。
        """
        if encrypted:
            raise ValueError("加密文档不写入明文全文索引")
        if sum(len(text) for text in pages) > 16_000_000:
            raise ValueError("文字量超过本地索引单篇上限")
        digest = hashlib.sha256(json.dumps(list(pages), ensure_ascii=False).encode()).hexdigest()
        canonical = str(Path(path).resolve())
        with self._db:
            self._db.execute(
                "INSERT INTO documents(path,title,digest) VALUES(?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET title=excluded.title,digest=excluded.digest",
                (canonical, title, digest),
            )
            self._db.execute("DELETE FROM pages WHERE path=?", (canonical,))
            self._db.executemany(
                "INSERT INTO pages(path,page,text) VALUES(?,?,?)",
                ((canonical, i, text) for i, text in enumerate(pages)),
            )
        return digest

    def set_labels(self, path: str, *, tags: Sequence[str], state: str, rating: int) -> None:
        if state not in ("未读", "在读", "已读") or rating not in range(6):
            raise ValueError("阅读状态或评分无效")
        if any(len(tag) > 80 for tag in tags) or len(tags) > 50:
            raise ValueError("标签过多或过长")
        with self._db:
            cursor = self._db.execute(
                "UPDATE documents SET tags=?,state=?,rating=? WHERE path=?",
                (
                    json.dumps(sorted(set(tags)), ensure_ascii=False),
                    state,
                    rating,
                    str(Path(path).resolve()),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("请先将当前文档加入索引")

    def records(self) -> list[LibraryRecord]:
        return [
            LibraryRecord(path, title, tuple(json.loads(tags)), state, rating)
            for path, title, tags, state, rating in self._db.execute(
                "SELECT path,title,tags,state,rating FROM documents ORDER BY title"
            )
        ]

    def search(self, query: str, limit: int = 200) -> list[LibraryHit]:
        if not query.strip() or not 1 <= limit <= 1000:
            return []
        if len(query) >= 3:
            # 双引号转义使用户输入成为短语，不执行 FTS 查询运算符。
            phrase = '"' + query.replace('"', '""') + '"'
            rows = self._db.execute(
                "SELECT p.path,p.page,snippet(pages,2,'','', ' … ',32),d.title "
                "FROM pages p JOIN documents d ON p.path=d.path WHERE pages MATCH ? LIMIT ?",
                (phrase, limit),
            )
        else:
            literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = self._db.execute(
                "SELECT p.path,p.page,substr(p.text,1,200),d.title FROM pages p "
                "JOIN documents d ON p.path=d.path WHERE p.text LIKE ? ESCAPE '\\' LIMIT ?",
                ("%" + literal + "%", limit),
            )
        return [
            LibraryHit(str(path), int(page), str(text), str(title))
            for path, page, text, title in rows
        ]

    def duplicate_texts(self) -> list[tuple[str, ...]]:
        groups: dict[str, list[str]] = {}
        for path, digest in self._db.execute("SELECT path,digest FROM documents"):
            groups.setdefault(digest, []).append(path)
        return [tuple(paths) for paths in groups.values() if len(paths) > 1]
