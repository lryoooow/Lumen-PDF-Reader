"""只读 Zotero 文献库：浏览合集 / 条目、找到 PDF 附件、读出 Zotero 里的批注。

Zotero 把数据存在「数据目录」里的 ``zotero.sqlite``（默认 ``~/Zotero``，可在 Zotero 设置里改，
记在配置文件 ``prefs.js`` 的 ``extensions.zotero.dataDir``）。Zotero 运行时会锁库，而且新改动
可能还在 WAL 里，所以先把库连同 ``-wal`` 复制到临时目录再读，读完删掉——**绝不写 Zotero 的库**。

表结构以 Zotero 源码 ``resource/schema/userdata.sql`` 为准：
``items`` / ``itemTypes`` / ``itemData`` + ``fields`` + ``itemDataValues``（题录字段）/
``itemCreators`` + ``creators`` / ``itemAttachments``（``storage:文件名`` 在 ``storage/<附件 key>/`` 下）/
``itemAnnotations``（``type`` 1 高亮 2 便签 3 图片 4 墨迹 5 下划线 6 文本；``position`` 是 JSON，
``{"pageIndex": 0, "rects": [[x1, y1, x2, y2], …]}``，PDF 用户空间、原点在左下）/
``collections`` + ``collectionItems``，回收站里的条目在 ``deletedItems`` / ``deletedCollections``。

纯标准库，可在后台线程里调用（每次读取各自打开、关闭连接）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from pdfreader.engines.types import PdfSpaceAnnotation

__all__ = [
    "ANNOTATION_KINDS",
    "ZoteroAnnotation",
    "ZoteroCollection",
    "ZoteroError",
    "ZoteroItem",
    "ZoteroLibrary",
    "ZoteroPdf",
    "find_data_dir",
    "to_pdf_space_annotations",
]

_logger = logging.getLogger("pdfreader")

# itemAnnotations.type → 名称（Zotero.Annotations.ANNOTATION_TYPE_*）
ANNOTATION_KINDS = {1: "highlight", 2: "note", 3: "image", 4: "ink", 5: "underline", 6: "text"}
_SKIP_TYPES = ("note", "annotation", "attachment")  # 列表里不作为「条目」显示的类型
_PREF = re.compile(r'user_pref\("(extensions\.zotero\.[A-Za-z]+)",\s*(.+?)\);')


class ZoteroError(Exception):
    """读取 Zotero 库失败（消息可直接给用户看）。"""


@dataclass(frozen=True)
class ZoteroCollection:
    id: int
    name: str
    parent_id: int | None


@dataclass(frozen=True)
class ZoteroPdf:
    """一个 PDF 附件。``path`` 为 None 表示只存了链接 / 文件不在本机。"""

    attachment_id: int
    path: Path | None
    title: str = ""

    @property
    def exists(self) -> bool:
        return self.path is not None and self.path.is_file()


@dataclass(frozen=True)
class ZoteroItem:
    id: int
    title: str
    creators: str = ""  # 「Vaswani, Shazeer 等」
    year: str = ""
    item_type: str = ""
    collections: tuple[int, ...] = ()
    pdfs: tuple[ZoteroPdf, ...] = ()


@dataclass(frozen=True)
class ZoteroAnnotation:
    """Zotero 里的一条批注（坐标为 PDF 用户空间，原点在页面左下）。"""

    kind: str  # highlight / underline / note / text / image / ink
    page_index: int
    rects: tuple[tuple[float, float, float, float], ...] = ()
    paths: tuple[tuple[float, ...], ...] = ()  # 墨迹：每笔 x, y, x, y, …
    text: str = ""  # 被标记的原文
    comment: str = ""
    color: str = "#ffd400"


# ---------------------------------------------------------------------- 找数据目录
def _profile_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(Path(appdata) / "Zotero" / "Zotero" / "Profiles")
    roots += [
        home / "Library" / "Application Support" / "Zotero" / "Profiles",
        home / ".zotero" / "zotero",
    ]
    return roots


def _read_prefs(prefs: Path) -> dict[str, str]:
    try:
        text = prefs.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values: dict[str, str] = {}
    for key, raw in _PREF.findall(text):
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = raw[1:-1]
        values[key] = str(raw)
    return values


def _prefs() -> dict[str, str]:
    for root in _profile_roots():
        if root.is_dir():
            for prefs in sorted(root.glob("*/prefs.js")):
                values = _read_prefs(prefs)
                if values:
                    return values
    return {}


def find_data_dir() -> Path | None:
    """本机 Zotero 数据目录（含 zotero.sqlite）；找不到返回 None。"""
    prefs = _prefs()
    candidates: list[Path] = []
    custom = prefs.get("extensions.zotero.dataDir", "")
    if custom and prefs.get("extensions.zotero.useDataDir", "false") == "true":
        candidates.append(Path(custom))
    candidates.append(Path.home() / "Zotero")
    for candidate in candidates:
        if (candidate / "zotero.sqlite").is_file():
            return candidate
    return None


# ---------------------------------------------------------------------- 读库
class ZoteroLibrary:
    """一个 Zotero 数据目录（只读）。"""

    def __init__(self, data_dir: Path, *, base_attachment_path: str | None = None) -> None:
        self._dir = data_dir
        self._db_path = data_dir / "zotero.sqlite"
        if base_attachment_path is None:
            base_attachment_path = _prefs().get("extensions.zotero.baseAttachmentPath", "")
        self._base = Path(base_attachment_path) if base_attachment_path else None

    @property
    def data_dir(self) -> Path:
        return self._dir

    @contextlib.contextmanager
    def _snapshot(self) -> Iterator[sqlite3.Connection]:
        """复制库（连同 WAL）到临时目录再打开：不碰 Zotero 正在用的文件。"""
        if not self._db_path.is_file():
            raise ZoteroError(f"找不到 Zotero 数据库：{self._db_path}")
        folder = Path(tempfile.mkdtemp(prefix="lumen_zotero_"))
        db: sqlite3.Connection | None = None
        try:
            copy = folder / "zotero.sqlite"
            try:
                shutil.copy2(self._db_path, copy)
                for suffix in ("-wal", "-shm"):
                    extra = Path(f"{self._db_path}{suffix}")
                    if extra.is_file():
                        shutil.copy2(extra, Path(f"{copy}{suffix}"))
                db = sqlite3.connect(copy)
                db.execute("SELECT 1 FROM items LIMIT 1")
            except (OSError, sqlite3.Error) as exc:
                _logger.warning("读取 Zotero 库失败：%s（%s）", self._db_path, exc)
                raise ZoteroError(f"无法读取 Zotero 库：{exc}") from exc
            yield db
        finally:
            if db is not None:
                db.close()
            shutil.rmtree(folder, ignore_errors=True)

    # ------------------------------------------------------------------ 条目与合集
    def load(self) -> tuple[list[ZoteroCollection], list[ZoteroItem]]:
        """读出全部合集与带 PDF 的条目（按添加时间新的在前，回收站里的不要）。"""
        with self._snapshot() as db:
            try:
                return self._collections(db), self._items(db)
            except sqlite3.Error as exc:
                raise ZoteroError(f"Zotero 库的结构不认识：{exc}") from exc

    @staticmethod
    def _collections(db: sqlite3.Connection) -> list[ZoteroCollection]:
        deleted = _ids(db, "SELECT collectionID FROM deletedCollections")
        rows = db.execute(
            "SELECT collectionID, collectionName, parentCollectionID FROM collections"
            " ORDER BY collectionName COLLATE NOCASE"
        ).fetchall()
        return [
            ZoteroCollection(int(cid), str(name), int(parent) if parent is not None else None)
            for cid, name, parent in rows
            if cid not in deleted
        ]

    def _items(self, db: sqlite3.Connection) -> list[ZoteroItem]:
        deleted = _ids(db, "SELECT itemID FROM deletedItems")
        rows = db.execute(
            "SELECT i.itemID, i.key, t.typeName FROM items i"
            " JOIN itemTypes t ON i.itemTypeID = t.itemTypeID ORDER BY i.dateAdded DESC"
        ).fetchall()
        types = {int(r[0]): (str(r[1]), str(r[2])) for r in rows if r[0] not in deleted}
        fields: dict[int, dict[str, str]] = {}
        for item_id, name, value in db.execute(
            "SELECT d.itemID, f.fieldName, v.value FROM itemData d"
            " JOIN fields f ON d.fieldID = f.fieldID"
            " JOIN itemDataValues v ON d.valueID = v.valueID"
            " WHERE f.fieldName IN ('title', 'date')"
        ):
            fields.setdefault(int(item_id), {})[str(name)] = str(value)
        creators: dict[int, list[str]] = {}
        for item_id, first, last in db.execute(
            "SELECT ic.itemID, c.firstName, c.lastName FROM itemCreators ic"
            " JOIN creators c ON ic.creatorID = c.creatorID ORDER BY ic.itemID, ic.orderIndex"
        ):
            name = str(last or first or "").strip()
            if name:
                creators.setdefault(int(item_id), []).append(name)
        member: dict[int, list[int]] = {}
        for cid, item_id in db.execute("SELECT collectionID, itemID FROM collectionItems"):
            member.setdefault(int(item_id), []).append(int(cid))
        pdfs: dict[int, list[ZoteroPdf]] = {}
        standalone: list[int] = []
        for att_id, parent, link_mode, content_type, path in db.execute(
            "SELECT itemID, parentItemID, linkMode, contentType, path FROM itemAttachments"
        ):
            if att_id in deleted or not _is_pdf(content_type, path):
                continue
            key = types.get(int(att_id), ("", ""))[0]
            pdf = ZoteroPdf(
                int(att_id),
                self._resolve(key, int(link_mode or 0), str(path or "")),
                fields.get(int(att_id), {}).get("title", ""),
            )
            if parent is None:
                standalone.append(int(att_id))
                pdfs.setdefault(int(att_id), []).append(pdf)
            elif int(parent) not in deleted:
                pdfs.setdefault(int(parent), []).append(pdf)
        items: list[ZoteroItem] = []
        for item_id, (_key, type_name) in types.items():
            if type_name in _SKIP_TYPES and item_id not in standalone:
                continue
            if not pdfs.get(item_id):  # 没有 PDF 的条目在阅读器里打不开，不列出
                continue
            data = fields.get(item_id, {})
            names = creators.get(item_id, [])
            who = "、".join(names[:2]) + (" 等" if len(names) > 2 else "")
            year = m.group(0) if (m := re.search(r"\d{4}", data.get("date", ""))) else ""
            title = data.get("title") or next(
                (p.path.stem for p in pdfs.get(item_id, ()) if p.path is not None), "（无标题）"
            )
            items.append(
                ZoteroItem(
                    item_id,
                    title,
                    who,
                    year,
                    type_name,
                    tuple(member.get(item_id, ())),
                    tuple(pdfs.get(item_id, ())),
                )
            )
        return items

    def _resolve(self, key: str, link_mode: int, raw: str) -> Path | None:
        """附件路径：导入的文件在 storage/<key>/ 下；链接的是绝对路径或相对「链接附件根目录」。"""
        if raw.startswith("storage:"):
            return self._dir / "storage" / key / raw[len("storage:") :]
        if raw.startswith("attachments:"):
            if self._base is None:
                return None
            return self._base / raw[len("attachments:") :]
        if link_mode == 2 and raw:  # 链接到文件（绝对路径）
            return Path(raw)
        return None

    # ------------------------------------------------------------------ 批注
    def annotations_for(self, pdf_path: Path) -> list[ZoteroAnnotation]:
        """这份 PDF 在 Zotero 里的批注（按 Zotero 的阅读顺序）；不在库里返回空列表。"""
        with self._snapshot() as db:
            try:
                att_id = self._attachment_for(db, pdf_path)
                if att_id is None:
                    return []
                deleted = _ids(db, "SELECT itemID FROM deletedItems")
                rows = db.execute(
                    "SELECT itemID, type, text, comment, color, position FROM itemAnnotations"
                    " WHERE parentItemID = ? ORDER BY sortIndex",
                    (att_id,),
                ).fetchall()
            except sqlite3.Error as exc:
                raise ZoteroError(f"Zotero 库的结构不认识：{exc}") from exc
        out: list[ZoteroAnnotation] = []
        for item_id, kind, text, comment, color, position in rows:
            if item_id in deleted:
                continue
            try:
                kind_id = int(kind)
            except (TypeError, ValueError):
                continue
            parsed = _annotation(kind_id, text, comment, color, position)
            if parsed is not None:
                out.append(parsed)
        return out

    def _attachment_for(self, db: sqlite3.Connection, pdf_path: Path) -> int | None:
        target = _norm(pdf_path)
        rows = db.execute(
            "SELECT a.itemID, i.key, a.linkMode, a.path FROM itemAttachments a"
            " JOIN items i ON a.itemID = i.itemID WHERE a.path IS NOT NULL"
        ).fetchall()
        for att_id, key, link_mode, raw in rows:
            path = self._resolve(str(key), int(link_mode or 0), str(raw))
            if path is not None and _norm(path) == target:
                return int(att_id)
        return None


def _annotation(
    kind_id: int, text: object, comment: object, color: object, position: object
) -> ZoteroAnnotation | None:
    kind = ANNOTATION_KINDS.get(kind_id)
    if kind is None:
        return None
    try:
        pos = json.loads(str(position))
        page = int(pos["pageIndex"])
        raw_rects = pos.get("rects") or ()
        raw_paths = pos.get("paths") or ()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    rects = tuple(r for r in (_rect(item) for item in raw_rects) if r is not None)
    paths = tuple(p for p in (_path(item) for item in raw_paths) if p is not None)
    if not rects and not paths:
        return None
    return ZoteroAnnotation(
        kind=kind,
        page_index=page,
        rects=rects,
        paths=paths,
        text=str(text or ""),
        comment=str(comment or ""),
        color=str(color or "#ffd400"),
    )


def _rect(raw: object) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        return float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
    except (TypeError, ValueError):
        return None


def _path(raw: object) -> tuple[float, ...] | None:
    if not isinstance(raw, list) or len(raw) < 4:
        return None
    try:
        return tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return None


def to_pdf_space_annotations(
    annotations: Sequence[ZoteroAnnotation],
) -> tuple[list[PdfSpaceAnnotation], int]:
    """把 Zotero 批注转成引擎可写入的形式：``(可导入的, 无法表示的条数)``。

    只做坐标与颜色转换，不碰 PDF；写盘由 ``Document.import_pdf_space_annotations`` 一次撤销完成。
    便签与文本框没有文字时无法表示，计入无法表示的条数。
    """
    items: list[PdfSpaceAnnotation] = []
    skipped = 0
    for annot in annotations:
        comment = annot.comment.strip()
        if annot.kind in ("note", "text"):
            comment = comment or annot.text.strip()
        if annot.kind == "ink":
            usable = bool(annot.paths)
        elif annot.kind == "text":
            usable = bool(annot.rects) and bool(comment)
        else:
            usable = bool(annot.rects)
        if not usable:
            skipped += 1
            continue
        items.append(
            PdfSpaceAnnotation(
                page_num=annot.page_index,
                kind=annot.kind,
                rects=annot.rects,
                paths=annot.paths,
                comment=comment,
                color=_rgb(annot.color),
            )
        )
    return items, skipped


def _rgb(color: str) -> tuple[float, float, float]:
    """``#RRGGBB``（Zotero 的批注颜色）→ 0–1 的 RGB；看不懂时用高亮黄。"""
    text = color.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    try:
        value = int(text[:6], 16)
    except ValueError:
        return (1.0, 0.83, 0.0)
    return ((value >> 16 & 0xFF) / 255, (value >> 8 & 0xFF) / 255, (value & 0xFF) / 255)


def _is_pdf(content_type: object, path: object) -> bool:
    return str(content_type or "") == "application/pdf" or str(path or "").lower().endswith(".pdf")


def _ids(db: sqlite3.Connection, sql: str) -> set[int]:
    try:
        return {int(r[0]) for r in db.execute(sql)}
    except sqlite3.Error:  # 旧版 Zotero 没有 deletedCollections 表
        return set()


def _norm(path: Path) -> str:
    return os.path.normcase(path.resolve())
