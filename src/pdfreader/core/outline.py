"""书签 / 大纲（Outline）模型。

PDF 的大纲（TOC）是树形结构。本模型定义纯 Python 的树节点，
大纲数据由引擎层提供（阶段后续实现引擎的大纲提取时填充）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

__all__ = ["OutlineItem", "empty_outline"]


@dataclass
class OutlineItem:
    """大纲单个条目（树节点）。

    Attributes:
        title: 条目标题
        page_num: 跳转目标页码（从 0 开始）；无目标时为 None
        level: 缩进层级（0 = 顶级）
        children: 子条目列表
    """

    title: str
    page_num: int | None = None
    level: int = 0
    children: list[OutlineItem] = field(default_factory=list)

    def add_child(self, child: OutlineItem) -> OutlineItem:
        """添加子条目，返回 child。"""
        child.level = self.level + 1
        self.children.append(child)
        return child

    def walk(self) -> Iterator[OutlineItem]:
        """深度优先遍历自身及所有后代（含自身）。"""
        yield self
        for child in self.children:
            yield from child.walk()


def empty_outline() -> OutlineItem:
    """返回一个表示"无大纲"的空根节点。"""
    return OutlineItem(title="(无大纲)", page_num=None, level=-1)
