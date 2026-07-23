"""单页领域模型。

封装一页的元信息（尺寸、旋转、页码）与按需加载的文本缓存。
页面渲染产物（位图）不在此缓存——那属于 GUI 层的 LRU 缓存职责（见 utils/cache.py）。

本模型纯 Python，通过依赖注入的 :class:`PdfEngine` 协议访问引擎，
不直接 import pymupdf。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from pdfreader.engines.base import EngineDocument, PdfEngine
from pdfreader.engines.types import PageText

__all__ = ["Page"]


@dataclass
class Page:
    """单页领域模型。

    Attributes:
        page_num: 页码（从 0 开始）
        width: 页面宽度（PDF 点）
        height: 页面高度（PDF 点）
        rotation: 旋转角度（0/90/180/270）
        is_dirty: 该页是否有未保存的修改（阶段 6 编辑功能用）
    """

    page_num: int
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0
    is_dirty: bool = False

    # 引擎与文档句柄通过依赖注入传入，不作为 dataclass 字段参与比较
    _engine: PdfEngine | None = field(default=None, repr=False, compare=False)
    _doc: EngineDocument | None = field(default=None, repr=False, compare=False)

    def bind(self, engine: PdfEngine, doc: EngineDocument) -> Page:
        """绑定引擎与文档句柄，使本页能按需加载文本。

        返回 self 以支持链式调用。绑定后 ``text`` 属性才可用。
        """
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_doc", doc)
        # 清除可能存在的旧缓存
        self.__dict__.pop("text", None)
        return self

    @cached_property
    def text(self) -> PageText:
        """按需加载并缓存该页的文本（首次访问时从引擎提取）。"""
        if self._engine is None or self._doc is None:
            raise RuntimeError("页未绑定引擎，无法加载文本。请先调用 bind()。")
        return self._engine.extract_text(self._doc, self.page_num)

    def invalidate_text_cache(self) -> None:
        """使文本缓存失效（编辑后调用，强制下次重新提取）。"""
        self.__dict__.pop("text", None)

    @property
    def aspect_ratio(self) -> float:
        """宽高比（width / height），height 为 0 时返回 0。"""
        if self.height == 0:
            return 0.0
        return self.width / self.height
