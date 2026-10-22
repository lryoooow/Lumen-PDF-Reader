"""基于 PyMuPDF 的引擎实现。

**整个项目唯一 ``import pymupdf`` 的地方**。将 PyMuPDF 的原生 API 翻译为
:mod:`pdfreader.engines.base` 定义的稳定接口，返回 :mod:`pdfreader.engines.types`
中的不可变值对象。原生引擎对象（``pymupdf.Document``）被封装在
:class:`PyMuPdfDocument` 内部，不外泄给上层。

线程安全提示：PyMuPDF 非线程安全，``PyMuPdfDocument`` 内的 ``Document`` 对象
生命周期必须限制在单个线程内：受控文档只在主线程使用，后台线程（搜索 / OCR / 转换）
各自打开自己的副本。
"""

from __future__ import annotations

import contextlib
import html
import logging
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    FREE_TEXT_FONTSIZE,
    MARKUP_TYPES,
    Annotation,
    AnnotationType,
    DocumentMeta,
    InkStroke,
    LinkInfo,
    PageText,
    PdfSpaceAnnotation,
    PixmapData,
    Point,
    Rect,
    SearchHit,
    SearchResult,
    TableData,
    TextSpan,
    TocEntry,
    WordBox,
)
from pdfreader.utils.text import has_cjk, is_cjk_char, join_words

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger("pdfreader")

__all__ = ["PyMuPdfDocument", "PyMuPdfEngine"]

# 1 PDF 点 = 1/72 英寸；zoom=1.0 对应 72 DPI
_BASE_DPI = 72.0

# PixmapData.n → PyMuPDF 颜色空间（用于把原始样本重新编码为图片）
_COLORSPACES = {1: pymupdf.csGRAY, 3: pymupdf.csRGB, 4: pymupdf.csCMYK}

# 非 Latin-1 文本（中文等）必须显式指定字体：base-14 的 helv 不含这些字形，
# PyMuPDF 会用 notdef 占位且**不抛异常**——页面上是一排方框，文本也回读不到。
# "china-s" 是 PyMuPDF 内置的简体中文黑体，无需外部字体文件（文件仅增大 1KB 量级）。
_CJK_FONTNAME = "china-s"

# 文字标记批注：种类 → (PyMuPDF 添加方法名, 默认颜色；None = PyMuPDF 默认黄色)
_MARKUP_KINDS: dict[str, tuple[str, tuple[float, float, float] | None]] = {
    "highlight": ("add_highlight_annot", None),
    "underline": ("add_underline_annot", (0.12, 0.45, 0.9)),
    "strikeout": ("add_strikeout_annot", (0.86, 0.2, 0.2)),
    "squiggly": ("add_squiggly_annot", (0.1, 0.65, 0.35)),
}

# 水印默认透明度（不透明的灰字会压在正文上，挡住阅读）
_WATERMARK_OPACITY = 0.25


def _cjk_fontname(text: str) -> str | None:
    """文本需要 CJK 字体时返回内置字体名，否则返回 None（沿用默认 helv）。"""
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        return _CJK_FONTNAME
    return None


def _noop_ensure_operation(pdf: object) -> None:
    """``ENSURE_OPERATION`` 的替代实现：什么都不做（保持调用签名兼容）。"""


_ENCRYPT_P_RE = re.compile(r"/P\s+(-?\d+)")


def _encrypt_dict(doc: pymupdf.Document) -> str | None:
    """返回 trailer 里 Encryption 字典的文本；未加密时返回 None。"""
    try:
        kind, value = doc.xref_get_key(-1, "Encrypt")  # -1 即 trailer
    except Exception:
        return None
    if kind == "dict":
        return str(value)
    if kind == "xref":  # /Encrypt 是间接对象
        try:
            return str(doc.xref_object(int(value.split()[0])))
        except Exception:
            return None
    return None


def _is_encrypted_file(doc: pymupdf.Document) -> bool:
    """文件是否带 /Encrypt——与「是否需要输入密码」是两件事。"""
    return _encrypt_dict(doc) is not None


def _raw_permissions(doc: pymupdf.Document) -> int | None:
    """从 Encryption 字典读原始 ``/P``（未加密 / 读不到时返回 None）。

    这个值与认证级别无关，正是 ``save(permissions=...)`` 期望的取值空间。
    """
    text = _encrypt_dict(doc)
    if text is None:
        return None
    match = _ENCRYPT_P_RE.search(text)
    return int(match.group(1)) if match else None


def _install_journal_guard_patch() -> None:
    """绕开 PyMuPDF 1.28.0 中必然抛错的日志守卫 ``ENSURE_OPERATION``。

    该守卫的实现是 ``JM_have_operation()`` → ``mupdf.pdf_undoredo_step(pdf, 0)``，
    而 ``pdf_undoredo_step`` 在 journal 开启后**两种状态都抛异常**：

    ================================ ==========================================================
    journal 开 + 不在 operation 内    Can't add a journal fragment absent an operation
    journal 开 + 在 operation 内      Can't undo/redo during an operation
    ================================ ==========================================================

    也就是说 journal 一开启，这个「守卫」就必然抛错（Python 胶水层与 MuPDF C 层版本错配）。
    它被 ``JM_insert_font`` / ``Document.get_new_xref`` / ``Document.update_object``
    调用，于是**所有需要新建字体或对象的操作**（插入 CJK 文本与中文水印、插入图片）
    在正常打开的文档上全部失败——而本引擎的 ``open()`` 一定会启用 journal 以支持撤销。

    这里把守卫降级为 no-op。代价只是少了一层 Python 提示：真在 operation 之外改文档时，
    MuPDF C 层依旧会抛 ``Can't add a journal fragment absent an operation``，不会静默写坏
    文档；而引擎的所有修改都已由 :meth:`PyMuPdfEngine._op` 包进 operation。
    PyMuPDF 修好该守卫后，这一步应当删掉（`pytest` 中的 CJK 插入用例会持续覆盖）。
    """
    if getattr(pymupdf, "ENSURE_OPERATION", None) is None:
        return  # 未来版本改名或移除时不做处理
    pymupdf.ENSURE_OPERATION = _noop_ensure_operation


_install_journal_guard_patch()


# ----------------------------------------------------------------------
# 旋转页（/Rotate ≠ 0）的坐标约定
# ----------------------------------------------------------------------
# 本引擎对外**一律使用可见帧坐标**：即 ``page.rect`` 所在坐标系，与
# :meth:`PyMuPdfEngine.render_page` 产出的位图逐像素对应（GUI 的点击换算、
# 页面布局、缩略图都用这一套）。而 MuPDF 的两类 API 都不在这套坐标上：
#
# * **读文本**：``Page.get_text*`` / ``Page.search_for`` 会先把页面的 /Rotate
#   临时置 0 再建 TextPage，返回的是**未旋转帧**坐标；而这一步本身就是在写
#   /Rotate，journal 开启时（本引擎 ``open()`` 必开，为的是支持撤销）MuPDF C 层
#   会拒绝：``Can't alter an object other than in an operation``。于是旋转页上
#   取词 / 取文本 / 搜索**全部报错**——而且发生在读取路径上，用户只看到
#   「选不中字」「搜索失败」，与「旋转」这个动作看不出关系。
# * **写内容**：批注与页面内容流天生按**未旋转帧**存储，显示时由 /Rotate 整体旋转。
#
# 所以这里统一收口：读走 :func:`_textpage`（不写 /Rotate，返回可见帧），
# 写用 :func:`_unrotated_point` / :func:`_unrotated_rect` 折算回未旋转帧，
# 并按页面旋转角给文字/图片补 ``rotate``，否则内容会被页面旋转带歪
# （实测 /Rotate=90 的页上，插字必须 ``rotate=90`` 才是屏幕上的横排）。


def _normalized_rotation(page: pymupdf.Page) -> int:
    """页面旋转角，归一化到 0/90/180/270。"""
    return int(page.rotation) % 360


def _textpage(page: pymupdf.Page, page_num: int, flags: int) -> pymupdf.TextPage:
    """按**可见帧**建立文本页，且不触碰 /Rotate（journal 开启时也安全）。

    走 ``Page._get_textpage``：它就是 PyMuPDF ``Page.get_textpage`` 内部真正建页的
    那一步，只是**不**做「临时把 /Rotate 置 0」的小动作。公开 API 在旋转页上必崩
    （见上文），而内部方法返回的坐标恰好是本引擎约定的可见帧。

    ``flags`` 要与原调用点一致（``TEXTFLAGS_WORDS`` / ``TEXTFLAGS_TEXT`` /
    ``TEXTFLAGS_DICT`` / ``TEXTFLAGS_SEARCH``），否则词切分、去连字符等行为会变。

    未来 PyMuPDF 若移除该方法：未旋转页退回公开 API（两者等价、坐标一致）；
    旋转页则明确报错，绝不返回坐标系不同的数据让上层静默画错位置。
    """
    build_textpage = getattr(page, "_get_textpage", None)
    if build_textpage is not None:
        try:
            raw = build_textpage(None, flags=flags, matrix=pymupdf.Matrix(1, 1))
            return pymupdf.TextPage(raw)
        except Exception as exc:
            raise RenderError(f"提取第 {page_num} 页文本失败：{exc}") from exc
    if _normalized_rotation(page) == 0:
        return page.get_textpage(flags=flags)
    raise RenderError(
        f"提取第 {page_num} 页文本失败：当前 PyMuPDF 版本没有可用的内部文本页接口，"
        "旋转页（/Rotate≠0）无法在保留撤销日志的前提下取文本"
    )


def _unrotated_point(page: pymupdf.Page, point: Point) -> pymupdf.Point:
    """可见帧点 → 未旋转帧点（写批注 / 内容流用）。"""
    return pymupdf.Point(point.x, point.y) * page.derotation_matrix


def _unrotated_points(page: pymupdf.Page, points: tuple[Point, ...]) -> list[pymupdf.Point]:
    """可见帧点序列 → 未旋转帧点序列（手写批注用）。"""
    return [_unrotated_point(page, p) for p in points]


def _unrotated_rect(page: pymupdf.Page, rect: Rect) -> pymupdf.Rect:
    """可见帧矩形 → 未旋转帧矩形（同上）。"""
    return pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1) * page.derotation_matrix


_FREE_TEXT_FILL = (1.0, 0.98, 0.85)  # 浅米黄底，与便签一致的「贴纸」观感


def _add_free_text(
    page: pymupdf.Page,
    rect: Rect,
    text: str,
    *,
    fill: tuple[float, ...] | None = None,
) -> pymupdf.Annot:
    """在可见帧矩形处添加文本框（FreeText）批注。

    用富文本外观：普通模式只认 base-14 字体，中文会变方框或字距错乱；富文本由 MuPDF
    的排版引擎按字形回退到内置 CJK 字体，旋转页上配合 ``rotate`` 仍是屏幕横排。
    富文本模式下 /Contents 为空，这里补写纯文本，供批注列表 / 导出笔记 / 搜索使用。
    """
    rich = html.escape(text).replace("\n", "<br/>")
    annot = page.add_freetext_annot(
        _unrotated_rect(page, rect),
        rich,
        richtext=True,
        style=f"font-size:{FREE_TEXT_FONTSIZE:g}pt;font-family:sans-serif;color:#1A1A1A",
        rotate=_normalized_rotation(page),
        border_width=0.8,
        border_color=(0.55, 0.55, 0.55),
        fill_color=tuple(fill) if fill else _FREE_TEXT_FILL,
    )
    annot.set_info(content=text)
    annot.update()
    return annot


def _import_one(page: pymupdf.Page, item: PdfSpaceAnnotation) -> bool:
    """写入一条 PDF 用户空间坐标的外部批注；无法表示的返回 False。"""
    to_page = page.transformation_matrix
    rects = [
        pymupdf.Rect(*r).normalize() * to_page for r in item.rects if r[2] != r[0] or r[3] != r[1]
    ]
    rects = [r.normalize() for r in rects]
    kind = item.kind
    annot: pymupdf.Annot | None = None
    if kind in ("highlight", "underline") and rects:
        method = "add_highlight_annot" if kind == "highlight" else "add_underline_annot"
        annot = getattr(page, method)(rects)
        annot.set_colors(stroke=item.color)
    elif kind == "note" and rects:
        annot = page.add_text_annot(rects[0].tl, item.comment or " ")
        annot.set_colors(stroke=item.color)
    elif kind == "text" and rects and item.comment.strip():
        visible = rects[0] * page.rotation_matrix
        rect = Rect(float(visible.x0), float(visible.y0), float(visible.x1), float(visible.y1))
        _add_free_text(page, rect, item.comment)
        return True
    elif kind == "image" and rects:
        annot = page.add_rect_annot(rects[0])
        annot.set_colors(stroke=item.color)
        annot.set_border(width=1.2)
    elif kind == "ink" and item.paths:
        lines = [
            [pymupdf.Point(path[i], path[i + 1]) * to_page for i in range(0, len(path) - 1, 2)]
            for path in item.paths
        ]
        annot = page.add_ink_annot([[(p.x, p.y) for p in line] for line in lines if line])
        annot.set_colors(stroke=item.color)
    if annot is None:
        return False
    if item.comment and kind != "note":
        annot.set_info(content=item.comment)
    annot.update()
    return True


def _visible_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> Rect:
    """未旋转帧矩形 → 可见帧矩形（读批注用）。"""
    visible = pymupdf.Rect(rect) * page.rotation_matrix
    return Rect(float(visible.x0), float(visible.y0), float(visible.x1), float(visible.y1))


def _line_text(words: Sequence[tuple[float, ...]], hit: Rect) -> str:
    """从词表拼出命中矩形所在那一行的**完整**文本（用于搜索结果的片段显示）。

    先找与命中框相交的词，以它的 (block, line) 认定"那一行"，再取该行全部词。
    旧实现只取命中点左侧的词（片段被截在关键词处），且按坐标取词会在双栏排版里
    混入另一栏的文字。取不到时返回空串，调用方回退到查询词。
    """
    anchor = next(
        (
            w
            for w in words
            if float(w[1]) < hit.y1
            and float(w[3]) > hit.y0
            and float(w[0]) < hit.x1
            and float(w[2]) > hit.x0
        ),
        None,
    )
    if anchor is None:
        return ""
    key = (int(anchor[5]), int(anchor[6]))
    same_line = sorted((w for w in words if (int(w[5]), int(w[6])) == key), key=lambda w: int(w[7]))
    text = ""
    for w in same_line:
        piece = str(w[4])
        text = text + join_words(text, piece) + piece if text else piece
    return text.strip()


def _chars_by_line(textpage: pymupdf.TextPage) -> dict[tuple[int, int], list[dict[str, object]]]:
    """(block, line) → 该行的字符列表（含 bbox，可见帧）。用于把中文"整行词"拆成单字。"""
    result: dict[tuple[int, int], list[dict[str, object]]] = {}
    for b_idx, block in enumerate(textpage.extractRAWDICT().get("blocks", [])):
        if block.get("type") != 0:
            continue
        b_no = int(block.get("number", b_idx))
        for l_idx, line in enumerate(block.get("lines", [])):
            chars: list[dict[str, object]] = []
            for span in line.get("spans", []):
                chars.extend(span.get("chars", []))
            result[(b_no, l_idx)] = chars
    return result


def _split_cjk_word(
    word: tuple[float, ...], line_chars: Sequence[dict[str, object]]
) -> list[WordBox]:
    """把含中文的"词"拆成单字（中文）与连续的非中文片段，各自带精确字框。

    PyMuPDF 只按空白切词，一整行中文是一个词——不拆的话用户无法选中/高亮行内的一部分。
    匹配不到字符时退回原词（宁可粗也不丢）。
    """
    x0, y0, x1, y1 = (float(v) for v in word[:4])
    block, line, word_no = int(word[5]), int(word[6]), int(word[7])
    original = WordBox(Rect(x0, y0, x1, y1), str(word[4]), block, line, word_no)

    inside: list[tuple[str, Rect]] = []
    for ch in line_chars:
        bbox = ch.get("bbox")
        if not isinstance(bbox, (tuple, list)) or len(bbox) != 4:
            continue
        bx0, by0, bx1, by1 = (float(v) for v in bbox)
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
        if x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5:
            inside.append((str(ch.get("c", "")), Rect(bx0, by0, bx1, by1)))
    if not inside:
        return [original]

    pieces: list[WordBox] = []
    run_text = ""
    run_rect: Rect | None = None

    def flush() -> None:
        nonlocal run_text, run_rect
        if run_text.strip() and run_rect is not None:
            pieces.append(WordBox(run_rect, run_text, block, line, word_no))
        run_text, run_rect = "", None

    for char, rect in inside:
        if not char.strip():
            flush()
        elif is_cjk_char(char):
            flush()
            pieces.append(WordBox(rect, char, block, line, word_no))
        else:
            run_text += char
            run_rect = (
                rect
                if run_rect is None
                else Rect(
                    min(run_rect.x0, rect.x0),
                    min(run_rect.y0, rect.y0),
                    max(run_rect.x1, rect.x1),
                    max(run_rect.y1, rect.y1),
                )
            )
    flush()
    return pieces or [original]


def _annot_color(annot: pymupdf.Annot) -> tuple[float, float, float] | None:
    """批注的显示颜色（优先 stroke，其次 fill）。"""
    try:
        colors = annot.colors or {}
    except Exception:
        return None
    for key in ("stroke", "fill"):
        value = colors.get(key)
        if value and len(value) >= 3:
            return (float(value[0]), float(value[1]), float(value[2]))
    return None


def _marked_text(page: pymupdf.Page, annot: pymupdf.Annot, words: Sequence[tuple[Any, ...]]) -> str:
    """文字标记批注覆盖的原文：取中心落在任一标记 quad（折算到可见帧）内的词。"""
    try:
        vertices = annot.vertices or []
    except Exception:
        vertices = []
    quads: list[pymupdf.Rect] = []
    for i in range(0, len(vertices) - 3, 4):
        quads.append(pymupdf.Quad(vertices[i : i + 4]).rect * page.rotation_matrix)
    if not quads:
        quads = [pymupdf.Rect(annot.rect) * page.rotation_matrix]
    picked = [
        w
        for w in words
        if any(
            q.x0 <= (float(w[0]) + float(w[2])) / 2 <= q.x1
            and q.y0 <= (float(w[1]) + float(w[3])) / 2 <= q.y1
            for q in quads
        )
    ]
    picked.sort(key=lambda w: (int(w[5]), int(w[6]), int(w[7])))
    text = ""
    prev_line: tuple[int, int] | None = None
    for w in picked:
        piece = str(w[4])
        line = (int(w[5]), int(w[6]))
        if not text:
            text = piece
        else:
            text += ("\n" if line != prev_line else join_words(text, piece)) + piece
        prev_line = line
    return text.strip()


class PyMuPdfDocument:
    """PyMuPDF 文档句柄封装。

    持有原生的 :class:`pymupdf.Document`，对其访问做基本的边界与状态检查。
    上层通过 :class:`PdfEngine` 协议操作文档，不应直接使用本类的内部属性。
    """

    __slots__ = ("_auth_level", "_doc", "_encrypted", "_password", "_path", "_permissions")

    def __init__(
        self,
        doc: pymupdf.Document,
        path: Path,
        *,
        password: str | None = None,
        auth_level: int = 0,
        encrypted: bool = False,
        permissions: int | None = None,
    ) -> None:
        self._doc = doc
        self._path = path
        self._password = password
        self._auth_level = auth_level
        self._encrypted = encrypted
        self._permissions = permissions

    @property
    def path(self) -> Path:
        return self._path

    @property
    def password(self) -> str | None:
        """打开本文件时使用的密码（未加密文档为 None）。

        整份保存（另存为 / 快照）时用它重新加密，否则加密文档会被静默写成明文。
        """
        return self._password

    @property
    def auth_level(self) -> int:
        """认证级别：0=未认证，2=用户密码，4=所有者密码（PyMuPDF 约定）。"""
        return self._auth_level

    @property
    def permissions(self) -> int | None:
        """原文档的权限位（Encryption 字典里的 ``/P``）；未加密时返回 None。

        刻意读原始的 ``/P`` 而不是 ``doc.permissions``：后者在用**所有者密码**打开时
        被 MuPDF 报成 ``-4``（等同于全部允许），回传给 ``save(permissions=...)``
        就会把受限文档保存成完全放开。实测该值可直接回传且幂等。

        这是**打开时**记录的快照，因此合并（底层文档被重开）之后依然有效。
        """
        return self._permissions

    @property
    def is_encrypted(self) -> bool:
        """源文件是否带 /Encrypt（打开时记录的快照）。

        不能用 ``native.is_encrypted``：PyMuPDF 那个属性的含义是「加密且尚未认证」，
        认证成功后会被置为 False，无法用来判断「这个文件原本是不是加密的」。
        """
        return self._encrypted

    def replace_native(self, doc: pymupdf.Document) -> None:
        """替换底层文档（合并时必须丢弃 journal 重开，见 :meth:`PyMuPdfEngine.merge`）。

        加密快照（password / permissions）刻意保留：合并后的内存文档已不带 /Encrypt，
        保存时要靠这些信息按原密码与权限重新加密，否则会写成明文或放开权限。
        """
        self._doc = doc
        self._auth_level = 0  # 重开后的文档不再处于「已认证」状态

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
            raise FileOpenError(f"文件不存在：{path.name}")

        try:
            doc = pymupdf.open(path)
        except pymupdf.FileDataError as exc:
            raise CorruptPdfError(f"文件损坏或不是合法 PDF：{path.name}") from exc
        except Exception as exc:
            _logger.warning("打开 %s 失败", path, exc_info=True)
            raise FileOpenError(f"无法打开文件 {path.name}：{exc}") from exc

        # 加密文档处理。注意「需要密码」与「文件是加密的」是两件事：
        # 只设了所有者密码（用户密码为空）的文件 needs_pass 为 False，但保存时
        # 依然必须保留加密，因此加密状态和 /P 都在这里一次性取好。
        encrypted = _is_encrypted_file(doc)
        permissions = _raw_permissions(doc)
        password_used: str | None = None
        auth_level = 0
        if doc.needs_pass:
            if password is None:
                doc.close()
                raise PasswordRequiredError(f"PDF 需要密码：{path.name}")
            auth_level = int(doc.authenticate(password) or 0)
            if not auth_level:
                doc.close()
                raise PasswordRequiredError(f"密码错误：{path.name}", needs_password=True)
            password_used = password

        # 启用 PyMuPDF 日志（撤销/重做的原生支持）
        with contextlib.suppress(Exception):
            if doc.is_pdf:
                doc.journal_enable()

        return PyMuPdfDocument(
            doc,
            path,
            password=password_used,
            auth_level=auth_level,
            encrypted=encrypted,
            permissions=permissions,
        )

    # ------------------------------------------------------------------
    # 撤销 / 重做（PyMuPDF 日志）
    # ------------------------------------------------------------------
    @contextlib.contextmanager
    def _op(self, handle: PyMuPdfDocument, name: str) -> Iterator[None]:
        """把一次修改包进日志操作，使其可撤销。日志未启用时直接执行。"""
        native = handle.native
        started = False
        try:
            native.journal_start_op(name)
            started = True
        except Exception:  # 日志未启用 / 不支持：直接改
            pass
        try:
            yield
        finally:
            if started:
                with contextlib.suppress(Exception):
                    native.journal_stop_op()

    def undo(self, doc: EngineDocument) -> bool:
        handle = self._require(doc)
        try:
            handle.native.journal_undo()
            return True
        except Exception:
            return False

    def redo(self, doc: EngineDocument) -> bool:
        handle = self._require(doc)
        try:
            handle.native.journal_redo()
            return True
        except Exception:
            return False

    def can_undo(self, doc: EngineDocument) -> bool:
        handle = self._require(doc)
        try:
            pos, _steps = handle.native.journal_position()
            return bool(pos > 0)
        except Exception:
            return False

    def can_redo(self, doc: EngineDocument) -> bool:
        handle = self._require(doc)
        try:
            pos, steps = handle.native.journal_position()
            return bool(pos < steps)
        except Exception:
            return False

    def history_position(self, doc: EngineDocument) -> int:
        """撤销历史中的当前位置；日志不可用（如合并后重建）时返回 0。"""
        handle = self._require(doc)
        try:
            pos, _steps = handle.native.journal_position()
            return int(pos)
        except Exception:
            return 0

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
            is_encrypted=handle.is_encrypted,
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

    def pixmap_to_png(self, data: PixmapData) -> bytes:
        """把 PixmapData 重新编码为 PNG。

        PyMuPDF 只接受 ``Pixmap(colorspace, width, height, samples, alpha)`` 形式，
        且第 5 个位置参数是 alpha 而不是 stride：把 stride 传进去会抛
        "Unrecognised args for constructing Pixmap"（旧实现在这里悄悄失败，
        OCR 每页都被判定为渲染失败而跳过）。
        """
        colorspace = _COLORSPACES.get(data.n)
        if colorspace is None:
            raise RenderError(f"不支持的像素通道数：{data.n}")
        row_bytes = data.width * data.n
        samples = data.samples
        if data.stride != row_bytes:  # 行对齐：按 stride 逐行取出紧凑样本
            samples = b"".join(
                data.samples[i * data.stride : i * data.stride + row_bytes]
                for i in range(data.height)
            )
        try:
            pix = pymupdf.Pixmap(colorspace, data.width, data.height, samples, False)
            png: bytes = pix.tobytes("png")
            return png
        except Exception as exc:
            # PyMuPDF 的报错会把整幅像素样本拼进消息（可达数 MB），这里截断
            raise RenderError(f"编码 PNG 失败：{str(exc)[:200]}") from exc

    def images_to_pdf(self, image_paths: Sequence[Path], output_path: Path) -> int:
        """把若干图片合成一个 PDF（每张图一页）。"""
        try:
            out = pymupdf.open()
            try:
                for img_path in image_paths:
                    page = out.new_page()
                    page.insert_image(page.rect, filename=str(img_path))
                out.save(str(output_path), garbage=4, deflate=True)
            finally:
                out.close()
        except Exception as exc:
            raise FileOpenError(f"图片转 PDF 失败：{exc}") from exc
        return len(image_paths)

    # ------------------------------------------------------------------
    # 文本提取
    # ------------------------------------------------------------------
    def extract_text(self, doc: EngineDocument, page_num: int) -> PageText:
        handle = self._require(doc)
        page = handle.get_page(page_num)

        try:
            full_text: str = _textpage(page, page_num, pymupdf.TEXTFLAGS_TEXT).extractText()
        except Exception as exc:
            raise RenderError(f"提取第 {page_num} 页文本失败：{exc}") from exc

        spans = self._extract_spans(page, page_num)
        return PageText(page_num=page_num, full_text=full_text, spans=spans)

    def iter_pages_text(self, doc: EngineDocument) -> Iterator[PageText]:
        handle = self._require(doc)
        for page_num in range(handle.page_count):
            yield self.extract_text(doc, page_num)

    def extract_tables(self, doc: EngineDocument, page_num: int) -> tuple[TableData, ...]:
        handle = self._require(doc)
        original = handle.get_page(page_num)
        if not handle.native.is_pdf:
            raise PdfEngineError("当前格式不支持表格提取")
        try:
            # find_tables 内部可能临时调整旋转。仅复制当前页到无 journal 的内存文档，
            # 不碰受控文档的 /Rotate，也不回退到磁盘旧版本或落地明文副本。
            with pymupdf.open() as snapshot:
                snapshot.insert_pdf(handle.native, from_page=page_num, to_page=page_num)
                page = snapshot[0]
                page.set_rotation(0)
                tables: list[TableData] = []
                for table in page.find_tables().tables:
                    values = table.extract()
                    if len(values) > 2000 or sum(len(row) for row in values) > 20000:
                        raise PdfEngineError("表格超过单次提取上限，请缩小文档范围")
                    rows = tuple(tuple(str(cell or "") for cell in row) for row in values)
                    if table.header.external:
                        rows = (tuple(str(cell or "") for cell in table.header.names), *rows)
                    tables.append(
                        TableData(page_num, _visible_rect(original, pymupdf.Rect(table.bbox)), rows)
                    )
                return tuple(tables)
        except PdfEngineError:
            raise
        except Exception as exc:
            raise PdfEngineError("表格提取失败") from exc

    def get_words(self, doc: EngineDocument, page_num: int) -> tuple[WordBox, ...]:
        """提取页面上的词及坐标（PyMuPDF: (x0,y0,x1,y1,word,block,line,word_no)）。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            textpage = _textpage(page, page_num, pymupdf.TEXTFLAGS_WORDS)
            raw = textpage.extractWORDS()
            # 中文没有空格分词：含 CJK 的"词"按字符框拆开，选择/高亮才能精确到字
            line_chars = _chars_by_line(textpage) if any(has_cjk(str(w[4])) for w in raw) else None
        except Exception as exc:
            raise RenderError(f"提取第 {page_num} 页文本词失败：{exc}") from exc
        words: list[WordBox] = []
        for w in raw:
            text = str(w[4])
            if not text.strip():
                continue
            if line_chars is not None and has_cjk(text):
                words.extend(_split_cjk_word(w, line_chars.get((int(w[5]), int(w[6])), [])))
                continue
            words.append(
                WordBox(
                    rect=Rect(float(w[0]), float(w[1]), float(w[2]), float(w[3])),
                    text=text,
                    block=int(w[5]),
                    line=int(w[6]),
                    word_no=int(w[7]),
                )
            )
        # 按阅读顺序排序（块 → 行 → 词序）；拆出的单字共享原词序号，稳定排序保持先后
        words.sort(key=lambda wb: (wb.block, wb.line, wb.word_no))
        return tuple(words)

    @staticmethod
    def _extract_spans(page: pymupdf.Page, page_num: int) -> tuple[TextSpan, ...]:
        """从文本页的 'dict' 提取带坐标的文本片段（可见帧坐标）。"""
        spans: list[TextSpan] = []
        try:
            data = _textpage(page, page_num, pymupdf.TEXTFLAGS_DICT).extractDICT()
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
    def search(
        self,
        doc: EngineDocument,
        query: str,
        *,
        cancel: Callable[[], bool] | None = None,
    ) -> SearchResult:
        """全文搜索；``cancel`` 逐页检查，返回 True 时停止并返回已找到的部分结果。

        ``SearchHit.text`` 填**命中所在行的实际文本**（由同一文本页的词表拼出），
        面板据此显示片段；拼不出时退回查询词。
        """
        if not query:
            return SearchResult(query=query, hits=())

        handle = self._require(doc)
        hits: list[SearchHit] = []
        for page_num in range(handle.page_count):
            if cancel is not None and cancel():
                break  # 被取消：调用方（后台线程）不会使用这份部分结果
            page = handle.get_page(page_num)
            try:
                # TextPage.search(quads=0) 的返回与 page.search_for 一致（同一行的命中
                # 会合并），但不改 /Rotate，旋转页上不会抛
                # "Can't alter an object other than in an operation"。
                textpage = _textpage(page, page_num, pymupdf.TEXTFLAGS_SEARCH)
                rects = textpage.search(query, quads=0)
                words = textpage.extractWORDS() if rects else []
            except Exception as exc:
                raise SearchError(f"搜索第 {page_num} 页失败：{exc}") from exc

            for rect in rects:
                hit_rect = Rect(rect.x0, rect.y0, rect.x1, rect.y1)
                hits.append(
                    SearchHit(
                        page_num=page_num,
                        rect=hit_rect,
                        text=_line_text(words, hit_rect) or query,
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
            with self._op(handle, "delete_page"):
                handle.native.delete_page(page_num)
        except Exception as exc:
            raise PdfEngineError(f"删除第 {page_num} 页失败：{exc}") from exc

    def rotate_page(self, doc: EngineDocument, page_num: int, degrees: int) -> None:
        if degrees % 90 != 0:
            raise PdfEngineError(f"旋转角度必须是 90 的倍数，得到：{degrees}")
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            with self._op(handle, "rotate_page"):
                page.set_rotation((int(page.rotation) + degrees) % 360)
        except Exception as exc:
            raise PdfEngineError(f"旋转第 {page_num} 页失败：{exc}") from exc

    def merge(self, doc: EngineDocument, other_path: Path) -> int:
        """把另一个 PDF 追加到末尾，返回追加页数。

        ``insert_pdf`` 与撤销日志不兼容，必须把本文档从内存重开（丢弃日志，合并后不可撤销）。
        因此**先**完整校验对方文件（能打开 / 未加密 / 非空），通过后才动本文档：
        旧实现先重开再打开对方，对方是加密或损坏文件时合并失败，撤销历史却已白白丢掉。
        重开时先建好新文档再关旧的，任何一步失败都不会留下已关闭的句柄。
        """
        handle = self._require(doc)
        if not other_path.exists():
            raise FileOpenError(f"要合并的文件不存在：{other_path.name}")
        try:
            other = pymupdf.open(other_path)
        except Exception as exc:
            _logger.warning("打开要合并的文件 %s 失败", other_path, exc_info=True)
            raise CorruptPdfError(
                f"无法打开要合并的文件 {other_path.name}（文件损坏或不是 PDF）"
            ) from exc
        try:
            if other.needs_pass:
                raise PasswordRequiredError(f"要合并的文件已加密，暂不支持：{other_path.name}")
            added = int(other.page_count)
            if added == 0 or not other.is_pdf:
                raise PdfEngineError(f"要合并的文件没有可用页面：{other_path.name}")
            native = handle.native
            journaled = False
            with contextlib.suppress(Exception):
                journaled = bool(native.journal_is_enabled())
            try:
                if journaled:
                    reopened = pymupdf.open(stream=native.tobytes(), filetype="pdf")
                    handle.replace_native(reopened)
                    native.close()
                    native = reopened
                native.insert_pdf(other)
            except Exception as exc:
                raise PdfEngineError(f"合并 {other_path.name} 失败：{exc}") from exc
            return added
        finally:
            other.close()

    def add_text_watermark(
        self,
        doc: EngineDocument,
        page_num: int | Sequence[int],
        text: str,
        *,
        opacity: float = _WATERMARK_OPACITY,
    ) -> None:
        """给一页或多页加居中的半透明文字水印（多页时是**一次**可撤销操作）。

        字宽用字体真实度量计算（中文按全角宽）——旧实现按 0.6em/字估算，中文水印明显偏右；
        旧实现也没有透明度，灰字会盖住正文。
        """
        handle = self._require(doc)
        pages = [page_num] if isinstance(page_num, int) else list(page_num)
        if not pages:
            raise PdfEngineError("没有要加水印的页面")
        targets = [handle.get_page(n) for n in pages]  # 含边界检查
        fontname = _cjk_fontname(text)
        fontsize = 40
        try:
            text_w = float(
                pymupdf.get_text_length(text, fontname=fontname or "helv", fontsize=fontsize)
            )
        except Exception:
            text_w = fontsize * len(text) * (1.0 if fontname else 0.6)
        extra: dict[str, object] = {"fontname": fontname} if fontname is not None else {}
        try:
            with self._op(handle, "watermark"):
                for page in targets:
                    rect = page.rect
                    page.insert_text(
                        _unrotated_point(page, Point((rect.width - text_w) / 2, rect.height / 2)),
                        text,
                        fontsize=fontsize,
                        color=(0.55, 0.55, 0.55),
                        fill_opacity=opacity,
                        stroke_opacity=opacity,
                        overlay=True,
                        rotate=_normalized_rotation(page),
                        **extra,
                    )
        except Exception as exc:
            raise PdfEngineError(f"添加水印失败：{exc}") from exc

    def save(self, doc: EngineDocument, path: Path, *, incremental: bool = False) -> None:
        """保存文档。

        加密文档的加密必须显式保留：增量保存用 ``PDF_ENCRYPT_KEEP`` 即可，
        但**整份保存**（另存为、合并后的内存文档、后台线程用的快照）如果不带
        ``encryption``，PyMuPDF 会写出**明文**，用户会以为文件仍受密码保护。
        因此只要打开时用到了密码，整份保存就用同一密码按 AES-256 重新加密，
        并原样带回原始 ``/P`` 权限位（实测幂等；省略该参数会把受限文档存成完全放开）。
        """
        handle = self._require(doc)
        target = Path(path)
        is_original = self._is_original_file(handle, target)
        if is_original and not self._can_incremental(handle):
            # MuPDF 打开时修复过的文件只能整份写出，而整份写回正被打开的原文件又不被允许
            raise FileOpenError(
                f"“{target.name}”在打开时已被自动修复，无法直接覆盖保存，请另存为新文件"
            )
        try:
            # 目标就是打开时的原文件：只能增量保存（PyMuPDF 不允许整份覆盖正在打开的文件）。
            # 「另存为」选中原文件也走这里，否则必报 "save to original must be incremental"
            if is_original:
                # 增量保存（存回原文件）：不可整理垃圾/改加密，仅追加变更
                handle.native.save(
                    str(target), incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP
                )
            else:
                # 目标已不是打开时的原文件（另存为之后 / 合并后的内存文档）：
                # 增量保存必然报 "incremental needs original file"，必须整份写出
                handle.native.save(
                    str(target), garbage=4, deflate=True, **self._encrypt_kwargs(handle)
                )
        except Exception as exc:
            _logger.warning("保存到 %s 失败", target, exc_info=True)
            raise FileOpenError(f"保存到 {target.name} 失败：{exc}") from exc

    @staticmethod
    def _can_incremental(handle: PyMuPdfDocument) -> bool:
        try:
            return bool(handle.native.can_save_incrementally())
        except Exception:
            return False

    @staticmethod
    def _encrypt_kwargs(handle: PyMuPdfDocument) -> dict[str, object]:
        """整份保存时的加密参数（明文文档返回空字典，行为与以前一致）。

        分两种情况：

        * 底层文档**仍带 /Encrypt**（正常打开的加密文件，含只设所有者密码的那种）：
          用 ``PDF_ENCRYPT_KEEP`` 原样保留，实测用户密码、所有者密码、``/P``
          全部与源文件一致（重新加密做不到这一点：拿不到原始的所有者密码，
          只能让用户输入的密码兼任所有者密码，等于给用户密码提权）。
        * 加密已经丢失（合并时 ``tobytes()`` 重开，实测输出不再加密）：
          只能用打开时记录的密码重新加密，并按记录的 ``/P`` 回填权限位——
          少了 ``permissions`` 参数 MuPDF 会写成 ``-4``（完全放开）。
        """
        if not handle.is_encrypted:
            return {}
        if _is_encrypted_file(handle.native):
            return {"encryption": pymupdf.PDF_ENCRYPT_KEEP}
        # 只设所有者密码的文件其用户密码为空，这里回填空串才是忠实的
        password = handle.password or ""
        kwargs: dict[str, object] = {
            "encryption": pymupdf.PDF_ENCRYPT_AES_256,
            "user_pw": password,
            "owner_pw": password,
        }
        if handle.permissions is not None:
            kwargs["permissions"] = handle.permissions
        return kwargs

    @staticmethod
    def _is_original_file(handle: PyMuPdfDocument, path: Path) -> bool:
        """该路径是否仍是文档打开时绑定的文件（增量保存的前提）。"""
        try:
            original = handle.native.name
        except Exception:
            return False
        if not original:  # 合并后是内存/流式文档，没有对应文件
            return False
        try:
            return Path(original).resolve() == path.resolve()
        except OSError:
            return False

    # ------------------------------------------------------------------
    # 批注操作
    # ------------------------------------------------------------------
    def add_highlight(
        self,
        doc: EngineDocument,
        page_num: int,
        rect: Rect | Sequence[Rect],
        *,
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加高亮（可见帧坐标）。传多个矩形（多行）时生成**一条**多段高亮，一次撤销即可。"""
        self.add_markup(doc, page_num, rect, kind="highlight", color=color)

    def add_markup(
        self,
        doc: EngineDocument,
        page_num: int,
        rects: Rect | Sequence[Rect],
        *,
        kind: str = "highlight",
        color: tuple[float, float, float] | None = None,
    ) -> None:
        """添加文字标记批注：highlight / underline / strikeout / squiggly。"""
        if kind not in _MARKUP_KINDS:
            raise PdfEngineError(f"不支持的标记类型：{kind}")
        method_name, default_color = _MARKUP_KINDS[kind]
        items = [rects] if isinstance(rects, Rect) else list(rects)
        if not items:
            raise PdfEngineError("没有要标记的文字区域")
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            with self._op(handle, kind):
                annot = getattr(page, method_name)([_unrotated_rect(page, r) for r in items])
                stroke = color or default_color
                if stroke is not None:
                    annot.set_colors(stroke=stroke)
                    annot.update()
        except Exception as exc:
            raise PdfEngineError(f"添加标记失败：{exc}") from exc

    def set_annotation_content(
        self, doc: EngineDocument, page_num: int, index: int, text: str
    ) -> None:
        """修改第 index 个批注的笔记内容（高亮附注 / 便签正文），可撤销。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        annots = list(page.annots())
        if not 0 <= index < len(annots):
            raise PdfEngineError(f"第 {page_num + 1} 页没有第 {index + 1} 个批注")
        try:
            with self._op(handle, "annot_content"):
                annot = annots[index]
                if annot.type and int(annot.type[0]) == AnnotationType.FREE_TEXT:
                    # 文本框的外观由富文本生成，改 /Contents 不会重绘：原位置、原底色重建
                    rect = _visible_rect(page, annot.rect)
                    fill = (annot.colors or {}).get("fill") or None
                    page.delete_annot(annot)
                    _add_free_text(page, rect, text, fill=tuple(fill) if fill else None)
                else:
                    annot.set_info(content=text)
                    annot.update()
        except Exception as exc:
            raise PdfEngineError(f"修改批注失败：{exc}") from exc

    def import_pdf_space_annotations(
        self, doc: EngineDocument, items: Sequence[PdfSpaceAnnotation]
    ) -> int:
        """批量导入外部批注（坐标为 PDF 用户空间），整批一次撤销；返回实际添加的条数。

        ``page.transformation_matrix`` 把 PDF 用户空间（左下原点，含 MediaBox 偏移）折算到
        未旋转帧——正是 PyMuPDF 写批注要的坐标系，旋转页无需再处理。超出页数、没有区域的条目跳过。
        """
        handle = self._require(doc)
        native = handle.native
        added = 0
        try:
            with self._op(handle, "import_annots"):
                for item in items:
                    if not 0 <= item.page_num < native.page_count:
                        continue
                    page = handle.get_page(item.page_num)
                    if _import_one(page, item):
                        added += 1
        except Exception as exc:
            raise PdfEngineError(f"导入批注失败：{exc}") from exc
        return added

    def add_ink(self, doc: EngineDocument, page_num: int, strokes: tuple[InkStroke, ...]) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            # PyMuPDF ink 格式: list[list[(x, y)]]，坐标按未旋转帧解释
            lines = [
                [(p.x, p.y) for p in _unrotated_points(page, stroke.points)] for stroke in strokes
            ]
            with self._op(handle, "ink"):
                page.add_ink_annot(lines)
        except Exception as exc:
            raise PdfEngineError(f"添加手写失败：{exc}") from exc

    def add_text_annot(self, doc: EngineDocument, page_num: int, point: Point, text: str) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            with self._op(handle, "note"):
                page.add_text_annot(_unrotated_point(page, point), text)
        except Exception as exc:
            raise PdfEngineError(f"添加便签失败：{exc}") from exc

    def add_free_text(
        self,
        doc: EngineDocument,
        page_num: int,
        rect: Rect,
        text: str,
        *,
        fill: tuple[float, float, float] | None = None,
    ) -> None:
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            with self._op(handle, "freetext"):
                _add_free_text(page, rect, text, fill=fill)
        except Exception as exc:
            raise PdfEngineError(f"添加文本框失败：{exc}") from exc

    def list_annotations(self, doc: EngineDocument, page_num: int) -> list[Annotation]:
        """列出该页批注（可见帧坐标）；文字标记类附带被标记的原文。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        result: list[Annotation] = []
        words: list[tuple[Any, ...]] | None = None
        try:
            for i, annot in enumerate(page.annots()):
                info = annot.info or {}
                a_type = int(annot.type[0]) if annot.type else 0
                marked = ""
                if a_type in MARKUP_TYPES:
                    if words is None:
                        # 与取词同一套（中文已拆到单字）：整行中文若是一个「词」，
                        # 其中心常落在高亮框外，被标记的原文就提取不到
                        words = [
                            (
                                w.rect.x0,
                                w.rect.y0,
                                w.rect.x1,
                                w.rect.y1,
                                w.text,
                                w.block,
                                w.line,
                                w.word_no,
                            )
                            for w in self.get_words(doc, page_num)
                        ]
                    marked = _marked_text(page, annot, words)
                result.append(
                    Annotation(
                        page_num=page_num,
                        annot_type=a_type,
                        rect=_visible_rect(page, annot.rect),
                        content=str(info.get("content", "")),
                        title=str(info.get("title", "")),
                        stable_id=str(info.get("id", "")),
                        index=i,
                        color=_annot_color(annot),
                        marked_text=marked,
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
                with self._op(handle, "delete_annot"):
                    page.delete_annot(annots[index])
                return True
            return False
        except Exception as exc:
            raise PdfEngineError(f"删除批注失败：{exc}") from exc

    def annotation_index_at(self, doc: EngineDocument, page_num: int, point: Point) -> int | None:
        """返回点（可见帧坐标）命中的最上层批注索引；无则 None（用于橡皮擦/右键删除）。

        批注的 ``rect`` 是未旋转帧坐标，因此先把点折算回去再比对，
        否则旋转页上「点在批注上」永远命中不了。
        """
        handle = self._require(doc)
        page = handle.get_page(page_num)
        probe = _unrotated_point(page, point)
        hit: int | None = None
        try:
            for i, annot in enumerate(page.annots()):
                r = annot.rect
                if r.x0 <= probe.x <= r.x1 and r.y0 <= probe.y <= r.y1:
                    hit = i  # 后者绘制在上层，取最后命中的
        except Exception:
            _logger.warning("第 %d 页批注命中检测失败", page_num, exc_info=True)
            return None
        return hit

    def insert_image(
        self, doc: EngineDocument, page_num: int, rect: Rect, image_path: Path
    ) -> None:
        """在页面指定矩形处插入图片（用于签名 / 图片）。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            with self._op(handle, "image"):
                page.insert_image(
                    _unrotated_rect(page, rect),
                    filename=str(image_path),
                    keep_proportion=True,
                    overlay=True,
                    # 内容流写完还要被 /Rotate 整体转一次：补 rotate 才能保持正立
                    rotate=_normalized_rotation(page),
                )
        except Exception as exc:
            raise PdfEngineError(f"插入图片失败：{exc}") from exc

    def insert_text(
        self, doc: EngineDocument, page_num: int, point: Point, text: str, *, fontsize: int = 12
    ) -> None:
        """在页面指定点插入文字（用于日期/时间戳）。

        文本含非 Latin-1 字符（中文等）时改用内置 CJK 字体：默认的 base-14 ``helv``
        没有这些字形，PyMuPDF 会用 notdef 占位且不报错——页面上只剩方框。
        """
        handle = self._require(doc)
        page = handle.get_page(page_num)
        try:
            fontname = _cjk_fontname(text)
            extra = {"fontname": fontname} if fontname is not None else {}
            with self._op(handle, "text"):
                page.insert_text(
                    _unrotated_point(page, point),
                    text,
                    fontsize=fontsize,
                    color=(0.1, 0.1, 0.1),
                    overlay=True,
                    rotate=_normalized_rotation(page),
                    **extra,
                )
        except Exception as exc:
            raise PdfEngineError(f"插入文字失败：{exc}") from exc

    # ------------------------------------------------------------------
    # 目录与链接（科研阅读导航）
    # ------------------------------------------------------------------
    def get_toc(self, doc: EngineDocument) -> tuple[TocEntry, ...]:
        """文档目录（书签 / 大纲）。没有目录时返回空元组。"""
        handle = self._require(doc)
        native = handle.native
        try:
            raw = native.get_toc(simple=False)
        except Exception as exc:
            raise PdfEngineError(f"读取目录失败：{exc}") from exc
        count = int(native.page_count)
        entries: list[TocEntry] = []
        for item in raw:
            level, title, page_1 = int(item[0]), str(item[1]), int(item[2])
            dest = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
            page_num = page_1 - 1 if 1 <= page_1 <= count else -1
            entries.append(
                TocEntry(level, title.strip(), page_num, self._dest_y(handle, page_num, dest))
            )
        return tuple(entries)

    def get_links(self, doc: EngineDocument, page_num: int) -> tuple[LinkInfo, ...]:
        """页面上的链接（热区为可见帧坐标）：内部跳转（参考文献 / 图表 / 目录）与外部网址。"""
        handle = self._require(doc)
        page = handle.get_page(page_num)
        count = handle.page_count
        try:
            raw = page.get_links()
        except Exception as exc:
            raise PdfEngineError(f"读取第 {page_num + 1} 页链接失败：{exc}") from exc
        links: list[LinkInfo] = []
        for item in raw:
            r = item.get("from")
            if r is None:
                continue
            rect = Rect(float(r.x0), float(r.y0), float(r.x1), float(r.y1))
            uri = str(item.get("uri") or "")
            target = item.get("page")
            if uri and item.get("kind") == pymupdf.LINK_URI:
                links.append(LinkInfo(rect=rect, uri=uri))
            elif isinstance(target, int) and 0 <= target < count:
                links.append(
                    LinkInfo(rect=rect, page_num=target, y=self._dest_y(handle, target, item))
                )
        return tuple(links)

    @staticmethod
    def _dest_y(handle: PyMuPdfDocument, page_num: int, dest: dict[str, object]) -> float | None:
        """跳转目标在页内的纵坐标（可见帧）；拿不准时返回 None（跳到页首）。

        旋转页上 PyMuPDF 给出的 ``to`` 点坐标系不稳定，此时宁可跳页首，也不跳错位置。
        """
        point = dest.get("to")
        if page_num < 0 or point is None:
            return None
        try:
            page = handle.get_page(page_num)
            if _normalized_rotation(page) != 0:
                return None
            y = float(point.y)  # type: ignore[attr-defined]
        except Exception:
            return None
        return y if 0 <= y <= float(page.rect.height) else None

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
