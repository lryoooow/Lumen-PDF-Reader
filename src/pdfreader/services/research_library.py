"""用户主动建立的本地全文索引与文献标记；事务更新，不索引加密文档明文。"""

from __future__ import annotations

import hashlib
import json
import ntpath
import sqlite3
import sys
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
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                path TEXT PRIMARY KEY, title TEXT NOT NULL, digest TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT '未读',
                rating INTEGER NOT NULL DEFAULT 0);
            CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
                path UNINDEXED, page UNINDEXED, text, tokenize='trigram');
            """)
            if sys.platform == "win32":
                self._migrate_windows_paths()
        except sqlite3.Error:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _migrate_windows_paths(self) -> None:
        """在一个事务内归并旧键，保留冲突版本的原始元数据和全文。"""
        if self._db.execute("PRAGMA user_version").fetchone()[0] >= 1:
            return
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute("PRAGMA user_version").fetchone()[0] >= 1:
                return
            groups: dict[str, list[str]] = {}
            for (path,) in self._db.execute("SELECT path FROM documents ORDER BY rowid DESC"):
                groups.setdefault(ntpath.normcase(path), []).append(path)
            for key, paths in groups.items():
                if len(paths) == 1 and key == paths[0]:
                    continue
                if len(paths) > 1:
                    self._archive_aliases(paths)
                preferred = key if key in paths else paths[0]
                for path in paths:
                    if path != preferred:
                        self._db.execute("DELETE FROM pages WHERE path=?", (path,))
                        self._db.execute("DELETE FROM documents WHERE path=?", (path,))
                if key != preferred:
                    self._db.execute("UPDATE documents SET path=? WHERE path=?", (key, preferred))
                    self._db.execute("UPDATE pages SET path=? WHERE path=?", (key, preferred))
            self._db.execute("PRAGMA user_version=1")

    def _archive_aliases(self, paths: list[str]) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS windows_path_history (
                path TEXT PRIMARY KEY, title TEXT NOT NULL, digest TEXT NOT NULL,
                tags TEXT NOT NULL, state TEXT NOT NULL, rating INTEGER NOT NULL,
                pages TEXT NOT NULL)
            """)
        for path in paths:
            pages = self._db.execute(
                "SELECT page,text FROM pages WHERE path=? ORDER BY CAST(page AS INTEGER)", (path,)
            ).fetchall()
            self._db.execute(
                "INSERT INTO windows_path_history "
                "SELECT path,title,digest,tags,state,rating,? FROM documents WHERE path=?",
                (json.dumps(pages, ensure_ascii=False), path),
            )

    @staticmethod
    def _path_key(path: str) -> str:
        resolved = str(Path(path).resolve())
        return ntpath.normcase(resolved) if sys.platform == "win32" else resolved

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
        canonical = self._path_key(path)
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
                    self._path_key(path),
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
        query = query.strip()
        if not query or not 1 <= limit <= 1000:
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
                "SELECT p.path,p.page,substr(p.text,max(1,instr(lower(p.text),lower(?))-70),200),"
                "d.title FROM pages p "
                "JOIN documents d ON p.path=d.path WHERE p.text LIKE ? ESCAPE '\\' LIMIT ?",
                (query, "%" + literal + "%", limit),
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
