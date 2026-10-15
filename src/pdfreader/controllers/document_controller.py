"""文档控制器。

协调 GUI 与业务层：接收用户动作（打开 / 编辑 / 批注 / 保存），调用 core 完成工作，
把失败原因记录到 :attr:`DocumentController.last_error` 供界面展示（不静默吞掉）。

「当前页」属于视图状态，由 :class:`~pdfreader.gui.widgets.pdf_view.PdfView` 维护；
控制器不再记录它（旧实现把「最后渲染的那一页」当作当前页，名不副实）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from PySide6.QtCore import QObject, Signal

from pdfreader.core import Document
from pdfreader.engines import PyMuPdfEngine
from pdfreader.engines.exceptions import (
    CorruptPdfError,
    FileOpenError,
    PasswordRequiredError,
)
from pdfreader.engines.types import (
    Annotation,
    InkStroke,
    LinkInfo,
    PixmapData,
    Point,
    Rect,
    TocEntry,
    WordBox,
)
from pdfreader.utils.text import join_words

__all__ = ["DocumentController", "OpenResult"]


def join_words_list(pieces: list[str]) -> str:
    """把同一行的词拼成文本（中文之间不加空格）。"""
    text = ""
    for piece in pieces:
        text = text + join_words(text, piece) + piece if text else piece
    return text


_logger = logging.getLogger("pdfreader")

_T = TypeVar("_T")


class OpenResult:
    """打开文件的结果分类（用于 GUI 给出精确反馈）。"""

    SUCCESS = "success"
    NOT_FOUND = "not_found"
    CORRUPT = "corrupt"
    NEEDS_PASSWORD = "needs_password"
    WRONG_PASSWORD = "wrong_password"
    UNKNOWN_ERROR = "unknown_error"


class DocumentController(QObject):
    """管理当前打开的文档。

    Signals:
        documentOpened(int): 文档成功打开，发送页数
        documentClosed(): 文档已关闭
        openFailed(str, str): 打开失败，发送 (结果分类, 错误消息)
        undoStateChanged(): 撤销/重做可用性变化（供按钮刷新）
    """

    documentOpened = Signal(int)  # page_count
    documentClosed = Signal()
    openFailed = Signal(str, str)  # (OpenResult 分类, 消息)
    undoStateChanged = Signal()

    def __init__(self, engine=None, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        # 默认使用 PyMuPDF 引擎；可注入用于测试
        self._engine = engine if engine is not None else PyMuPdfEngine()
        self._document: Document | None = None
        self._last_error: str = ""

    @property
    def last_error(self) -> str:
        """最近一次失败操作的原因（供 GUI 展示）。"""
        return self._last_error

    @property
    def is_modified(self) -> bool:
        return self._document is not None and self._document.is_modified

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------
    @property
    def has_document(self) -> bool:
        return self._document is not None and not self._document.is_closed

    @property
    def document(self) -> Document | None:
        return self._document

    @property
    def page_count(self) -> int:
        return self._document.page_count if self._document else 0

    @property
    def title(self) -> str:
        return self._document.title if self._document else ""

    @property
    def password(self) -> str | None:
        """当前文档打开时用的密码（后台线程重新打开文件时需要）；无文档或未加密为 None。"""
        return self._document.password if self.has_document and self._document else None

    # ------------------------------------------------------------------
    # 打开 / 关闭
    # ------------------------------------------------------------------
    def open_file(self, path: Path, password: str | None = None) -> bool:
        """打开 PDF 文件。

        Returns:
            True 表示成功打开（documentOpened 信号已发）；
            False 表示失败（openFailed 信号已发）。需要密码时分类为 ``NEEDS_PASSWORD``，
            提供了密码但不对时为 ``WRONG_PASSWORD``。
        """
        try:
            doc = Document.open(self._engine, path, password=password)
        except CorruptPdfError as exc:
            self.openFailed.emit(OpenResult.CORRUPT, str(exc))
            return False
        except PasswordRequiredError as exc:
            kind = OpenResult.NEEDS_PASSWORD if password is None else OpenResult.WRONG_PASSWORD
            self.openFailed.emit(kind, str(exc))
            return False
        except FileOpenError as exc:
            # FileOpenError 是基类；CorruptPdf/PasswordRequired 已在上面处理
            result = OpenResult.NOT_FOUND if "不存在" in str(exc) else OpenResult.UNKNOWN_ERROR
            self.openFailed.emit(result, str(exc))
            return False
        except Exception as exc:
            _logger.warning("打开 %s 失败", path, exc_info=True)
            self.openFailed.emit(OpenResult.UNKNOWN_ERROR, f"打开失败：{exc}")
            return False

        # 静默关闭旧文档（不发 documentClosed，避免切换文件时欢迎页闪烁）
        if self._document is not None and not self._document.is_closed:
            self._document.close()
        self._document = doc
        self.documentOpened.emit(doc.page_count)
        return True

    def close_document(self) -> None:
        """关闭当前文档（如有）。"""
        if self._document is not None and not self._document.is_closed:
            self._document.close()
        self._document = None
        self.documentClosed.emit()

    # ------------------------------------------------------------------
    # 渲染 / 只读查询
    # ------------------------------------------------------------------
    def render_page(self, page_num: int, zoom: float = 1.0) -> PixmapData:
        """渲染指定页。

        Raises:
            RuntimeError: 未打开文档
            IndexError: 页码越界
        """
        if self._document is None or self._document.is_closed:
            raise RuntimeError("未打开文档，无法渲染。")
        if page_num < 0 or page_num >= self._document.page_count:
            raise IndexError(f"页码越界：{page_num}，有效范围 0..{self._document.page_count - 1}")
        return self._document.render_page(page_num, zoom)

    def get_page_bounds(self) -> list[Rect]:
        """获取所有页的边界框列表（用于视图布局）。"""
        if self._document is None or self._document.is_closed:
            return []
        return self._document.get_all_page_bounds()

    def get_page_sizes(self) -> list[tuple[int, int]]:
        """获取所有页的原始尺寸 (width, height) @ 72dpi（用于视图布局）。"""
        return [(int(b.width), int(b.height)) for b in self.get_page_bounds()]

    def _query(self, what: str, fn: Callable[[Document], _T], default: _T) -> _T:
        """只读查询：失败时记日志并返回默认值（这些查询失败不应打断阅读）。"""
        if self._document is None or self._document.is_closed:
            return default
        try:
            return fn(self._document)
        except Exception:
            _logger.warning("%s失败", what, exc_info=True)
            return default

    def get_words(self, page_num: int) -> tuple[WordBox, ...]:
        """获取某页的词及坐标（文本选择/复制）；无文档或失败时返回空。"""
        return self._query("取词", lambda d: d.get_words(page_num), ())

    def list_annotations(self, page_num: int) -> list[Annotation]:
        return self._query("列举批注", lambda d: d.list_annotations(page_num), [])

    def list_all_annotations(self) -> list[Annotation]:
        """全文批注（批注面板 / 导出笔记用）。"""

        def collect(d: Document) -> list[Annotation]:
            result: list[Annotation] = []
            for page_num in range(d.page_count):
                result.extend(d.list_annotations(page_num))
            return result

        return self._query("列举全部批注", collect, [])

    def annotation_index_at(self, page_num: int, point: Point) -> int | None:
        return self._query("批注命中检测", lambda d: d.annotation_index_at(page_num, point), None)

    def get_toc(self) -> tuple[TocEntry, ...]:
        return self._query("读取目录", lambda d: d.get_toc(), ())

    def get_links(self, page_num: int) -> tuple[LinkInfo, ...]:
        return self._query("读取链接", lambda d: d.get_links(page_num), ())

    def text_in_rect(self, page_num: int, rect: Rect) -> str:
        """某页矩形内的文字（如链接热区上的「[12]」），按阅读顺序拼接。"""
        words = self.get_words(page_num)
        picked = [
            w.text
            for w in words
            if rect.contains(Point((w.rect.x0 + w.rect.x1) / 2, (w.rect.y0 + w.rect.y1) / 2))
        ]
        return join_words_list(picked)

    @property
    def is_encrypted(self) -> bool:
        """源文件是否加密（AI 助读据此在外发前二次确认）。"""
        doc = self._document
        return doc is not None and not doc.is_closed and doc.meta.is_encrypted

    def page_text(self, page_num: int) -> str:
        """整页文字（按引擎的阅读顺序：同一行拼接，行间换行，块间空行）。"""
        lines: list[str] = []
        current: tuple[int, int] | None = None
        block: int | None = None
        pieces: list[str] = []
        for w in self.get_words(page_num):
            key = (w.block, w.line)
            if key != current:
                if pieces:
                    lines.append(join_words_list(pieces))
                    pieces = []
                if block is not None and w.block != block:
                    lines.append("")
                current, block = key, w.block
            pieces.append(w.text)
        if pieces:
            lines.append(join_words_list(pieces))
        return "\n".join(lines)

    def page_texts(self) -> list[str]:
        """全部页的文字（AI 助读的上下文；主线程调用，PyMuPDF 非线程安全）。"""
        return [self.page_text(i) for i in range(self.page_count)]

    def text_excerpt(self, page_num: int, y: float | None, *, max_chars: int = 240) -> str:
        """链接目标处的原文摘录：从页内纵坐标 ``y``（None = 页首）起取几行文字。

        用于参考文献 / 图表引用的预览卡，让用户不跳转也能先看到引用的是什么。
        """
        words = self.get_words(page_num)
        if not words:
            return ""
        top = (y - 2.0) if y is not None else float("-inf")
        lines: dict[tuple[int, int], list[WordBox]] = {}
        for w in words:
            if w.rect.y1 >= top:
                lines.setdefault((w.block, w.line), []).append(w)
        ordered = sorted(
            lines.values(), key=lambda ws: (min(x.rect.y0 for x in ws), min(x.rect.x0 for x in ws))
        )
        text = ""
        for line in ordered:
            piece = join_words_list([w.text for w in sorted(line, key=lambda x: x.rect.x0)])
            text = f"{text} {piece}".strip() if text else piece
            if len(text) >= max_chars:
                return text[: max_chars - 1].rstrip() + "…"
        return text

    # ------------------------------------------------------------------
    # 统一执行（失败时记录真实原因，供 GUI 展示，不再静默吞掉）
    # ------------------------------------------------------------------
    def _run(self, op_name: str, fn: Callable[[Document], object]) -> bool:
        if self._document is None or self._document.is_closed:
            self._last_error = "未打开文档"
            return False
        try:
            fn(self._document)
            self._last_error = ""
            self.undoStateChanged.emit()
            return True
        except Exception as exc:  # 兜底：记录真实原因供 GUI 展示
            self._last_error = str(exc)
            _logger.warning("%s失败：%s", op_name, exc, exc_info=True)
            return False

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def delete_page(self, page_num: int) -> bool:
        return self._run("删除页", lambda d: d.delete_page(page_num))

    def rotate_page(self, page_num: int, degrees: int) -> bool:
        return self._run("旋转页", lambda d: d.rotate_page(page_num, degrees))

    def merge(self, other_path: Path) -> int:
        """合并另一个 PDF，返回追加页数（失败返回 0）。

        合并会重建底层文档（``insert_pdf`` 与撤销日志不兼容），之后不再可撤销；
        撤销按钮状态仍要靠 ``undoStateChanged`` 刷新。
        """
        added: list[int] = []
        ok = self._run("合并", lambda d: added.append(d.merge(other_path)))
        return added[0] if ok and added else 0

    def add_watermark(self, page_num: int | Sequence[int], text: str) -> bool:
        return self._run("添加水印", lambda d: d.add_watermark(page_num, text))

    # ------------------------------------------------------------------
    # 批注操作
    # ------------------------------------------------------------------
    def add_highlight(
        self,
        page_num: int,
        rect: Rect | Sequence[Rect],
        *,
        color: tuple[float, float, float] | None = None,
    ) -> bool:
        return self._run("添加高亮", lambda d: d.add_highlight(page_num, rect, color=color))

    def add_markup(
        self,
        page_num: int,
        rects: Rect | Sequence[Rect],
        *,
        kind: str = "highlight",
        color: tuple[float, float, float] | None = None,
    ) -> bool:
        return self._run(
            "添加标记", lambda d: d.add_markup(page_num, rects, kind=kind, color=color)
        )

    def add_ink(self, page_num: int, strokes: tuple[InkStroke, ...]) -> bool:
        return self._run("添加手写", lambda d: d.add_ink(page_num, strokes))

    def add_text_annot(self, page_num: int, point: Point, text: str) -> bool:
        return self._run("添加便签", lambda d: d.add_text_annot(page_num, point, text))

    def add_free_text(self, page_num: int, rect: Rect, text: str) -> bool:
        return self._run("添加文本框", lambda d: d.add_free_text(page_num, rect, text))

    def set_annotation_content(self, page_num: int, index: int, text: str) -> bool:
        return self._run("修改批注笔记", lambda d: d.set_annotation_content(page_num, index, text))

    def delete_annotation(self, page_num: int, index: int) -> bool:
        deleted: list[bool] = []
        ok = self._run("删除批注", lambda d: deleted.append(d.delete_annotation(page_num, index)))
        if ok and deleted and not deleted[0]:
            self._last_error = "该批注已不存在"
            return False
        return ok

    def insert_image(self, page_num: int, rect: Rect, image_path: Path) -> bool:
        return self._run("插入图片", lambda d: d.insert_image(page_num, rect, image_path))

    def insert_text(self, page_num: int, point: Point, text: str, *, fontsize: int = 12) -> bool:
        return self._run(
            "插入文字", lambda d: d.insert_text(page_num, point, text, fontsize=fontsize)
        )

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def save_as(self, path: Path) -> bool:
        """另存为；成功后把文档路径切到新文件（后续保存/搜索/OCR 均用它）。

        目标若正是打开时的原文件，引擎会自动走增量保存（整份覆盖正在打开的文件不被允许）。
        """
        if not self._run("保存", lambda d: d.save(path)):
            return False
        assert self._document is not None
        self._document.path = path
        return True

    def save_in_place(self) -> bool:
        """存回当前文件（增量保存）。"""
        if self._document is None or self._document.is_closed:
            self._last_error = "未打开文档"
            return False
        path = self._document.path
        return self._run("保存", lambda d: d.save(path, incremental=True))

    def save_copy(self, path: Path) -> bool:
        """把当前内存内容（含未保存改动）另存副本，不改变修改状态。"""
        return self._run("导出副本", lambda d: d.save_copy(path))

    # ------------------------------------------------------------------
    # 撤销 / 重做
    # ------------------------------------------------------------------
    def undo(self) -> bool:
        return self._history_step("撤销", lambda d: d.undo())

    def redo(self) -> bool:
        return self._history_step("重做", lambda d: d.redo())

    def _history_step(self, name: str, fn: Callable[[Document], bool]) -> bool:
        if self._document is None or self._document.is_closed:
            return False
        try:
            ok = fn(self._document)
        except Exception as exc:
            self._last_error = str(exc)
            _logger.warning("%s失败", name, exc_info=True)
            return False
        if ok:
            self.undoStateChanged.emit()
        return ok

    def can_undo(self) -> bool:
        return self._document is not None and self._document.can_undo()

    def can_redo(self) -> bool:
        return self._document is not None and self._document.can_redo()
