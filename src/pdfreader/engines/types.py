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
    "Annotation",
    "AnnotationType",
    "DocumentMeta",
    "InkStroke",
    "PageText",
    "PixmapData",
    "Point",
    "Rect",
    "SearchHit",
    "SearchResult",
    "TextSpan",
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
    """批注类型常量（对应 PyMuPDF 的 annot.type[0]）。"""

    TEXT = 0  # 文本便签
    HIGHLIGHT = 8  # 高亮
    UNDERLINE = 9  # 下划线
    STRIKEOUT = 11  # 删除线
    SQUIGGLY = 12  # 波浪线
    INK = 15  # 手写
    SQUARE = 14  # 矩形框
    CIRCLE = 13  # 圆形


@dataclass(frozen=True, slots=True)
class InkStroke:
    """手写笔迹的一条线（点序列）。"""

    points: tuple[Point, ...]


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
    """

    page_num: int
    annot_type: int
    rect: Rect
    content: str = ""
    title: str = ""
    strokes: tuple[InkStroke, ...] = field(default_factory=tuple)

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
        }
        return names.get(self.annot_type, f"类型{self.annot_type}")
