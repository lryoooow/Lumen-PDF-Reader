"""引擎层数据结构。

定义引擎与上层之间传递的**不可变值对象**。
这些类型与具体引擎解耦：无论底层是 PyMuPDF 还是其他引擎，
返回的都是这些统一的 dataclass，上层无需了解引擎原生类型。

设计要点：
- 全部使用 ``frozen=True`` 的 dataclass，防止上层误改引擎数据
- 字段类型明确，便于 mypy 严格检查与 IDE 提示
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = [
    "FREE_TEXT_FONTSIZE",
    "MARKUP_TYPES",
    "Annotation",
    "AnnotationType",
    "DocumentMeta",
    "InkStroke",
    "LinkInfo",
    "PageText",
    "PdfSpaceAnnotation",
    "PixmapData",
    "Point",
    "Rect",
    "SearchHit",
    "SearchResult",
    "TableData",
    "TextSpan",
    "TocEntry",
    "WordBox",
]


@dataclass(frozen=True, slots=True)
class Point:
    """二维点坐标（PDF 点单位，1 点 = 1/72 英寸）。"""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    """矩形区域（PDF 点单位）。遵循 PyMuPDF 约定：(x0, y0) 左上，(x1, y1) 右下。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def contains(self, point: Point) -> bool:
        """点是否在矩形内（含边界）。"""
        return self.x0 <= point.x <= self.x1 and self.y0 <= point.y <= self.y1


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    """文档元数据。"""

    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    creator: str = ""
    producer: str = ""
    page_count: int = 0
    is_encrypted: bool = False
    is_pdf: bool = True
    format: str = "PDF"  # PDF / XPS / EPUB 等


@dataclass(frozen=True, slots=True)
class PixmapData:
    """渲染得到的位图数据。

    像素数据以**原始字节**形式存放（行优先，从左上到右下）。
    上层（GUI）据此构造 QImage / QPixmap。注意构造时需用 ``stride`` 作为每行字节数。

    Attributes:
        samples: 像素字节序列（RGB 或 RGBA）
        width: 图像宽度（像素）
        height: 图像高度（像素）
        n: 每像素字节数（3=RGB，4=RGBA）
        stride: 每行字节数（= width * n，单独保留以兼容对齐场景）
    """

    samples: bytes
    width: int
    height: int
    n: int
    stride: int


@dataclass(frozen=True, slots=True)
class SearchHit:
    """单次搜索命中：在某页的某个矩形区域找到了关键词。"""

    page_num: int
    rect: Rect
    text: str = ""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次全文搜索的结果集合。"""

    query: str
    hits: tuple[SearchHit, ...] = field(default_factory=tuple)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    @property
    def page_numbers(self) -> tuple[int, ...]:
        """去重后的命中页码（升序）。"""
        return tuple(sorted({h.page_num for h in self.hits}))


@dataclass(frozen=True, slots=True)
class TextSpan:
    """一段连续文本片段及其在页面上的位置（用于选择/复制）。"""

    text: str
    rect: Rect


@dataclass(frozen=True, slots=True)
class TableData:
    page_num: int
    rect: Rect
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class WordBox:
    """单个词及其在页面上的位置（用于拖拽选择文本）。

    ``block`` / ``line`` / ``word_no`` 保留阅读顺序信息，
    便于把所选词序列重建为带换行的可读文本。
    """

    rect: Rect
    text: str
    block: int
    line: int
    word_no: int


@dataclass(frozen=True, slots=True)
class PageText:
    """一页的文本提取结果。"""

    page_num: int
    full_text: str
    spans: tuple[TextSpan, ...] = field(default_factory=tuple)

    def __iter__(self) -> Iterator[TextSpan]:
        return iter(self.spans)


# ---------------------------------------------------------------------------
# 批注（阶段 7）
# ---------------------------------------------------------------------------
class AnnotationType:
    """批注类型常量（与 PyMuPDF 的 ``PDF_ANNOT_*`` / ``annot.type[0]`` 一一对应）。"""

    TEXT = 0  # 文本便签
    FREE_TEXT = 2  # 文本框
    SQUARE = 4  # 矩形框
    CIRCLE = 5  # 圆形
    HIGHLIGHT = 8  # 高亮
    UNDERLINE = 9  # 下划线
    SQUIGGLY = 10  # 波浪线
    STRIKEOUT = 11  # 删除线
    STAMP = 13  # 图章
    INK = 15  # 手写


FREE_TEXT_FONTSIZE = 11.0  # 文本框批注字号（pt）；GUI 按它估算文本框大小

# 文字标记类批注（高亮 / 下划线 / 删除线 / 波浪线）：都覆盖一段文字，可提取被标记的原文
MARKUP_TYPES = frozenset(
    {
        AnnotationType.HIGHLIGHT,
        AnnotationType.UNDERLINE,
        AnnotationType.STRIKEOUT,
        AnnotationType.SQUIGGLY,
    }
)


@dataclass(frozen=True, slots=True)
class InkStroke:
    """手写笔迹的一条线（点序列）。"""

    points: tuple[Point, ...]


@dataclass(frozen=True, slots=True)
class PdfSpaceAnnotation:
    """要导入的外部批注（如 Zotero）：坐标为 **PDF 用户空间**（原点在页面左下，未旋转），
    与本项目其余接口的「可见帧」不同，只由引擎在写入时折算。

    Attributes:
        page_num: 页码（0 起）
        kind: highlight / underline / note / text / image / ink
        rects: (x0, y0, x1, y1) 列表；墨迹用 ``paths``（每笔 x, y, x, y, …）
        comment: 批注笔记；text 类型是框里显示的文字
        color: RGB（0–1）
    """

    page_num: int
    kind: str
    rects: tuple[tuple[float, float, float, float], ...] = ()
    paths: tuple[tuple[float, ...], ...] = ()
    comment: str = ""
    color: tuple[float, float, float] = (1.0, 0.83, 0.0)


@dataclass(frozen=True, slots=True)
class Annotation:
    """批注的引擎无关表示。

    Attributes:
        page_num: 所在页码（0-based）
        annot_type: AnnotationType 常量
        rect: 批注边界框（PDF 点单位）
        content: 文本内容（便签内容等）
        title: 作者
        strokes: 手写笔迹（仅 INK 类型有意义）
        index: 在该页批注列表中的序号（删除 / 编辑笔记时用）
        color: 批注颜色 (r, g, b)，0..1；未设置时为 None
        marked_text: 文字标记类批注覆盖的原文（高亮 / 下划线等），其他类型为空
    """

    page_num: int
    annot_type: int
    rect: Rect
    content: str = ""
    title: str = ""
    strokes: tuple[InkStroke, ...] = field(default_factory=tuple)
    index: int = -1
    color: tuple[float, float, float] | None = None
    marked_text: str = ""
    stable_id: str = ""  # PDF /NM；只在文档内结合来源指纹使用，不使用页内列表序号

    @property
    def is_markup(self) -> bool:
        """是否为文字标记类批注（高亮 / 下划线 / 删除线 / 波浪线）。"""
        return self.annot_type in MARKUP_TYPES

    @property
    def is_highlight(self) -> bool:
        return self.annot_type == AnnotationType.HIGHLIGHT

    @property
    def is_ink(self) -> bool:
        return self.annot_type == AnnotationType.INK

    @property
    def is_text(self) -> bool:
        return self.annot_type == AnnotationType.TEXT

    @property
    def type_name(self) -> str:
        names = {
            AnnotationType.TEXT: "便签",
            AnnotationType.HIGHLIGHT: "高亮",
            AnnotationType.UNDERLINE: "下划线",
            AnnotationType.STRIKEOUT: "删除线",
            AnnotationType.SQUIGGLY: "波浪线",
            AnnotationType.INK: "手写",
            AnnotationType.SQUARE: "矩形",
            AnnotationType.CIRCLE: "圆形",
            AnnotationType.FREE_TEXT: "文本框",
            AnnotationType.STAMP: "图章",
        }
        return names.get(self.annot_type, f"类型{self.annot_type}")


# ---------------------------------------------------------------------------
# 目录与链接（科研阅读：大纲导航、参考文献 / 图表交叉引用跳转）
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TocEntry:
    """目录（书签 / 大纲）的一项。

    Attributes:
        level: 层级（1 = 顶级）
        title: 标题
        page_num: 目标页码（0-based）；目标无效时为 -1
        y: 目标在页内的纵坐标（可见帧，PDF 点）；未知时为 None（跳到页首）
    """

    level: int
    title: str
    page_num: int
    y: float | None = None


@dataclass(frozen=True, slots=True)
class LinkInfo:
    """页面上的一个可点击链接。

    ``rect`` 为可见帧坐标。内部跳转时 ``page_num`` ≥ 0（``y`` 为目标纵坐标，可能未知）；
    外部链接时 ``uri`` 非空。
    """

    rect: Rect
    page_num: int = -1
    y: float | None = None
    uri: str = ""

    @property
    def is_internal(self) -> bool:
        return self.page_num >= 0

    @property
    def is_external(self) -> bool:
        return bool(self.uri)
