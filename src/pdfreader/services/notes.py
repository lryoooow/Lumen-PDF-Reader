"""阅读笔记导出（纯 Python）。

把文档里的批注整理成 Markdown：按页排列，高亮 / 下划线等给出被标记的原文（引用块）
与附注，便签给出正文。科研阅读里常用来把一篇论文的摘录与想法一次性带进笔记软件
（Obsidian / Notion / Typora 等）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from pdfreader.engines.types import Annotation, AnnotationType

__all__ = ["annotations_to_markdown"]


def _quote(text: str) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return "\n".join(f"> {line}" for line in lines)


def annotations_to_markdown(
    title: str,
    annotations: Sequence[Annotation],
    *,
    source: str = "",
    now: datetime | None = None,
) -> str:
    """生成 Markdown 笔记。没有可导出的批注时也返回带标题的文档（并注明无批注）。

    手写 / 图章等无文字内容的批注只列出类型与页码，便于回到原文查看。
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    out: list[str] = [f"# {title or '阅读笔记'}", ""]
    if source:
        out.append(f"- 来源：`{source}`")
    out.append(f"- 导出时间：{stamp}")
    out.append(f"- 批注数量：{len(annotations)}")
    out.append("")
    if not annotations:
        out.append("_（本文档暂无批注）_")
        return "\n".join(out) + "\n"

    current_page: int | None = None
    for annot in sorted(annotations, key=lambda a: (a.page_num, a.rect.y0, a.rect.x0)):
        if annot.page_num != current_page:
            current_page = annot.page_num
            out.extend([f"## 第 {current_page + 1} 页", ""])
        if annot.is_markup:
            out.append(f"**{annot.type_name}**")
            if annot.marked_text:
                out.append(_quote(annot.marked_text))
            if annot.content.strip():
                out.append("")
                out.append(f"笔记：{annot.content.strip()}")
        elif annot.annot_type in (AnnotationType.TEXT, AnnotationType.FREE_TEXT):
            out.append(f"**{annot.type_name}**：{annot.content.strip() or '（空）'}")
        else:
            out.append(
                f"**{annot.type_name}**" + (f"：{annot.content.strip()}" if annot.content else "")
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"
