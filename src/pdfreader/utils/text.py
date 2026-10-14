"""文本小工具（纯 Python，引擎与 GUI 共用）。

* :func:`is_cjk_char` / :func:`has_cjk` —— 判断中日韩字符。PyMuPDF 按空白切词，
  整行中文会被当成一个"词"；取词、拼接选中文本时需要据此特殊处理。
* :func:`join_words` —— 把同一行相邻的两段文本拼起来：两侧任一是 CJK 字符时不加空格。
* :func:`clean_copied_text` —— 科研阅读常用的「复制时合并换行」：去掉 PDF 排版换行、
  合并行尾连字符断词，便于粘贴到翻译工具或笔记里。
"""

from __future__ import annotations

import re

__all__ = ["clean_copied_text", "has_cjk", "is_cjk_char", "join_words"]

# 中日韩统一表意文字、扩展 A、兼容表意、假名、谚文、全角标点与符号
_CJK_RANGES = (
    (0x2E80, 0x2FDF),  # 部首
    (0x3000, 0x303F),  # CJK 标点
    (0x3040, 0x30FF),  # 平假名 / 片假名
    (0x3100, 0x312F),  # 注音
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本区
    (0xAC00, 0xD7AF),  # 谚文
    (0xF900, 0xFAFF),  # 兼容表意
    (0xFF00, 0xFFEF),  # 全角 ASCII / 全角标点
)


def is_cjk_char(ch: str) -> bool:
    """单个字符是否为中日韩字符（含全角标点）。空串返回 False。"""
    if not ch:
        return False
    code = ord(ch[0])
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def has_cjk(text: str) -> bool:
    """文本中是否含有中日韩字符。"""
    return any(is_cjk_char(ch) for ch in text)


def join_words(left: str, right: str) -> str:
    """同一行两段文本之间的分隔符：任一侧边界字符是 CJK 时为空，否则为空格。"""
    if not left or not right:
        return ""
    return "" if is_cjk_char(left[-1]) or is_cjk_char(right[0]) else " "


_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_PARAGRAPH = " "  # 段落占位符（处理期间保护空行）


def clean_copied_text(text: str) -> str:
    """合并 PDF 排版换行，保留空行分段。

    * ``inter-\\nnational`` → ``international``（行尾连字符断词）
    * 英文行与行之间换行 → 空格；中文行之间换行 → 直接相连
    * 连续空行视为段落分隔，保留为一个空行
    """
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n[ \t]*\n+", _PARAGRAPH, normalized)
    normalized = _HYPHEN_BREAK.sub(r"\1\2", normalized)

    parts = normalized.split("\n")
    merged = parts[0].rstrip()
    for part in parts[1:]:
        piece = part.strip()
        if not piece:
            continue
        merged = merged + join_words(merged.rstrip(_PARAGRAPH), piece) + piece
    merged = re.sub(r"[ \t]+", " ", merged)
    return merged.replace(_PARAGRAPH, "\n\n").strip()
