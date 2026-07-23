"""基于 PyMuPDF 的引擎实现。

**整个项目唯一 ``import pymupdf`` 的地方**。将 PyMuPDF 的原生 API 翻译为
:mod:`pdfreader.engines.base` 定义的稳定接口，返回 :mod:`pdfreader.engines.types`
中的不可变值对象。原生引擎对象（``pymupdf.Document``）被封装在
:class:`PyMuPdfDocument` 内部，不外泄给上层。

线程安全提示：PyMuPDF 非线程安全，``PyMuPdfDocument`` 内的 ``Document`` 对象
生命周期必须限制在单个线程内（见阶段 4 的渲染线程设计）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pymupdf

from pdfreader.engines.base import EngineDocument
from pdfreader.engines.exceptions import (
    CorruptPdfError,
    FileOpenError,
    PageIndexError,
    PasswordRequiredError,
    PdfEngineError,
    RenderError,
    SearchError,
)
from pdfreader.engines.types import (
    Annotation,
    DocumentMeta,
    InkStroke,
    PageText,
    PixmapData,
    Point,
    Rect,
    SearchHit,
    SearchResult,
    TextSpan,
)

if TYPE_CHECKING:
    pass

__all__ = ["PyMuPdfDocument", "PyMuPdfEngine"]

# 1 PDF 点 = 1/72 英寸；zoom=1.0 对应 72 DPI
_BASE_DPI = 72.0


class PyMuPdfDocument:
    """PyMuPDF 文档句柄封装。

    持有原生的 :class:`pymupdf.Document`，对其访问做基本的边界与状态检查。
    上层通过 :class:`PdfEngine` 协议操作文档，不应直接使用本类的内部属性。
    """

    __slots__ = ("_doc", "_path")

    def __init__(self, doc: pymupdf.Document, path: Path) -> None:
        self._doc = doc
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    @property
    def page_count(self) -> int:
        return int(self._doc.page_count)

    @property
    def native(self) -> pymupdf.Document:
        """原生 Document 对象（仅供引擎实现内部使用）。

        上层不应访问此属性——它暴露仅因为引擎方法需要在不同调用间共享同一文档。
        """
        return self._doc

    def get_page(self, page_num: int) -> pymupdf.Page:
        """获取页面，带边界检查。"""
        count = int(self._doc.page_count)
        if page_num < 0 or page_num >= count:
            raise PageIndexError(page_num, count)
        return self._doc[page_num]

    def close(self) -> None:
        if not self._doc.is_closed:
            self._doc.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()


class PyMuPdfEngine:
    """PyMuPDF 引擎实现。

    实现 :class:`pdfreader.engines.base.PdfEngine` 协议。
    无状态、线程安全的工厂类；文档状态全部封装在返回的 :class:`PyMuPdfDocument` 中。
    """

    # ------------------------------------------------------------------
    # 打开 / 关闭
    # ------------------------------------------------------------------
    def open(self, path: Path, password: str | None = None) -> PyMuPdfDocument:
        """打开 PDF 文件，必要时用密码解锁。

        Raises:
            FileOpenError: 文件不存在
            CorruptPdfError: 文件损坏或不是合法 PDF
            PasswordRequiredError: 需要密码但未提供/错误
        """
        if not path.exists():
            raise FileOpenError(f"文件不存在：{path}")

        try:
            doc = pymupdf.open(path)
        except pymupdf.FileDataError as exc:
            raise CorruptPdfError(f"文件损坏或不是合法 PDF：{path}") from exc
        except Exception as exc:
            raise FileOpenError(f"无法打开文件 {path}：{exc}") from exc

        # 加密文档处理
        if doc.needs_pass:
            if password is None:
                doc.close()
                raise PasswordRequiredError(f"PDF 需要密码：{path}")
            if not doc.authenticate(password):
                doc.close()
                raise PasswordRequiredError(f"密码错误：{path}", needs_password=True)

        return PyMuPdfDocument(doc, path)

    def close(self, doc: EngineDocument) -> None:
        """关闭文档。"""
        if isinstance(doc, PyMuPdfDocument):
            doc.close()

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    def metadata(self, doc: EngineDocument) -> DocumentMeta:
        handle = self._require(doc)
        native = handle.native
        md = native.metadata or {}
        return DocumentMeta(
            title=str(md.get("title") or ""),
            author=str(md.get("author") or ""),
            subject=str(md.get("subject") or ""),
            keywords=str(md.get("keywords") or ""),
            creator=str(md.get("creator") or ""),
            producer=str(md.get("producer") or ""),
            page_count=int(native.page_count),
            is_encrypted=bool(native.is_encrypted),
            is_pdf=bool(native.is_pdf),
            format=str(md.get("format") or "PDF"),
        )

    def page_count(self, doc: EngineDocument) -> int:
        return self._require(doc).page_count

    def page_bounds(self, doc: EngineDocument, page_num: int) -> Rect:
        """获取页面边界框（PDF 点单位）。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        rect = page.rect
        return Rect(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def render_page(self, doc: EngineDocument, page_num: int, zoom: float = 1.0) -> PixmapData:
        """渲染页面为位图。

        Args:
            zoom: 缩放系数。1.0 = 72 DPI。alpha=False 返回 RGB（n=3，省内存）。
        """
        if zoom <= 0:
            raise RenderError(f"缩放系数必须为正数，得到：{zoom}")

        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            matrix = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
        except PageIndexError:
            raise
        except Exception as exc:
            raise RenderError(f"渲染第 {page_num} 页失败：{exc}") from exc

        return PixmapData(
            samples=pix.samples,
            width=pix.width,
            height=pix.height,
            n=pix.n,
            stride=pix.stride,
        )

    # ------------------------------------------------------------------
    # 文本提取
    # ------------------------------------------------------------------
    def extract_text(self, doc: EngineDocument, page_num: int) -> PageText:
        handle = self._require(doc)
        page = handle.get_page(page_num)

        try:
            full_text: str = page.get_text("text")
        except Exception as exc:
            raise RenderError(f"提取第 {page_num} 页文本失败：{exc}") from exc

        spans = self._extract_spans(page, page_num)
        return PageText(page_num=page_num, full_text=full_text, spans=spans)

    def iter_pages_text(self, doc: EngineDocument) -> Iterator[PageText]:
        handle = self._require(doc)
        for page_num in range(handle.page_count):
            yield self.extract_text(doc, page_num)

    @staticmethod
    def _extract_spans(page: pymupdf.Page, page_num: int) -> tuple[TextSpan, ...]:
        """从 'dict' 提取带坐标的文本片段。"""
        spans: list[TextSpan] = []
        try:
            data = page.get_text("dict")
        except Exception:
            return tuple(spans)

        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 0 = 文本块
                continue
            for line in block.get("lines", []):
                line_text_parts: list[str] = []
                line_rects: list[pymupdf.Rect] = []
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue
                    line_text_parts.append(text)
                    bbox = span.get("bbox")
                    if bbox and len(bbox) == 4:
                        line_rects.append(pymupdf.Rect(bbox))

                if not line_text_parts or not line_rects:
                    continue

                # 合并一行的 bbox
                r0 = line_rects[0]
                for r in line_rects[1:]:
                    r0 = r0 | r
                spans.append(
                    TextSpan(
                        text="".join(line_text_parts),
                        rect=Rect(r0.x0, r0.y0, r0.x1, r0.y1),
                    )
                )
        return tuple(spans)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------
    def search(self, doc: EngineDocument, query: str) -> SearchResult:
        if not query:
            return SearchResult(query=query, hits=())

        handle = self._require(doc)
        hits: list[SearchHit] = []
        for page_num in range(handle.page_count):
            page = handle.get_page(page_num)
            try:
                rects = page.search_for(query)
            except Exception as exc:
                raise SearchError(f"搜索第 {page_num} 页失败：{exc}") from exc

            for rect in rects:
                hits.append(
                    SearchHit(
                        page_num=page_num,
                        rect=Rect(rect.x0, rect.y0, rect.x1, rect.y1),
                        text=query,
                    )
                )

        return SearchResult(query=query, hits=tuple(hits))

    # ------------------------------------------------------------------
    # 编辑操作
    # ------------------------------------------------------------------
    def delete_page(self, doc: EngineDocument, page_num: int) -> None:
        handle = self._require(doc)
        handle.get_page(page_num)  # 含边界检查
        try:
            handle.native.delete_page(page_num)
        except Exception as exc:
            raise PdfEngineError(f"删除第 {page_num} 页失败：{exc}") from exc

    def rotate_page(self, doc: EngineDocument, page_num: int, degrees: int) -> None:
        if degrees % 90 != 0:
            raise PdfEngineError(f"旋转角度必须是 90 的倍数，得到：{degrees}")
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            page.set_rotation((int(page.rotation) + degrees) % 360)
        except Exception as exc:
            raise PdfEngineError(f"旋转第 {page_num} 页失败：{exc}") from exc

    def merge(self, doc: EngineDocument, other_path: Path) -> int:
        handle = self._require(doc)
        if not other_path.exists():
            raise FileOpenError(f"要合并的文件不存在：{other_path}")
        try:
            other = pymupdf.open(other_path)
            added = int(other.page_count)
            handle.native.insert_pdf(other)
            other.close()
            return added
        except Exception as exc:
            raise PdfEngineError(f"合并 {other_path} 失败：{exc}") from exc

    def add_text_watermark(self, doc: EngineDocument, page_num: int, text: str) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            rect = page.rect
            # 居中半透明水印
            fontsize = 40
            text_w = fontsize * len(text) * 0.6
            x = (rect.width - text_w) / 2
            y = rect.height / 2
            page.insert_text(
                (x, y), text, fontsize=fontsize, color=(0.85, 0.85, 0.85), overlay=True
            )
        except Exception as exc:
            raise PdfEngineError(f"添加水印失败：{exc}") from exc

    def save(self, doc: EngineDocument, path: Path, *, incremental: bool = False) -> None:
        handle = self._require(doc)
        try:
            handle.native.save(str(path), garbage=4, deflate=True, incremental=incremental)
        except Exception as exc:
            raise FileOpenError(f"保存到 {path} 失败：{exc}") from exc

    # ------------------------------------------------------------------
    # 批注操作
    # ------------------------------------------------------------------
    def add_highlight(self, doc: EngineDocument, page_num: int, rect: Rect) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            page.add_highlight_annot(pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1))
        except Exception as exc:
            raise PdfEngineError(f"添加高亮失败：{exc}") from exc

    def add_ink(self, doc: EngineDocument, page_num: int, strokes: tuple[InkStroke, ...]) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            # PyMuPDF ink 格式: list[list[(x, y)]]
            lines = [[(p.x, p.y) for p in stroke.points] for stroke in strokes]
            page.add_ink_annot(lines)
        except Exception as exc:
            raise PdfEngineError(f"添加手写失败：{exc}") from exc

    def add_text_annot(self, doc: EngineDocument, page_num: int, point: Point, text: str) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            page.add_text_annot(pymupdf.Point(point.x, point.y), text)
        except Exception as exc:
            raise PdfEngineError(f"添加便签失败：{exc}") from exc

    def list_annotations(self, doc: EngineDocument, page_num: int) -> list[Annotation]:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        result: list[Annotation] = []
        try:
            for annot in page.annots():
                info = annot.info or {}
                a_type = int(annot.type[0]) if annot.type else 0
                r = annot.rect
                result.append(
                    Annotation(
                        page_num=page_num,
                        annot_type=a_type,
                        rect=Rect(float(r.x0), float(r.y0), float(r.x1), float(r.y1)),
                        content=str(info.get("content", "")),
                        title=str(info.get("title", "")),
                    )
                )
        except Exception as exc:
            raise PdfEngineError(f"列举批注失败：{exc}") from exc
        return result

    def delete_annotation(self, doc: EngineDocument, page_num: int, index: int) -> bool:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            annots = list(page.annots())
            if 0 <= index < len(annots):
                page.delete_annot(annots[index])
                return True
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _require(doc: EngineDocument) -> PyMuPdfDocument:
        """校验并取回原生文档句柄。"""
        if not isinstance(doc, PyMuPdfDocument):
            raise TypeError(
                f"引擎期望 PyMuPdfDocument，得到 {type(doc).__name__}。"
                " 不同引擎的文档句柄不可混用。"
            )
        return doc
