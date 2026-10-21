"""文本小工具（纯 Python，引擎与 GUI 共用）。

* :func:`is_cjk_char` / :func:`has_cjk` —— 判断中日韩字符。PyMuPDF 按空白切词，
  整行中文会被当成一个"词"；取词、拼接选中文本时需要据此特殊处理。
* :func:`join_words` —— 把同一行相邻的两段文本拼起来：两侧任一是 CJK 字符时不加空格。
* :func:`clean_copied_text` —— 科研阅读常用的「复制时合并换行」：去掉 PDF 排版换行、
  合并行尾连字符断词，便于粘贴到翻译工具或笔记里。
* :func:`normalize_pdf_text` —— 展开排版连字（ﬁ → fi）、去掉软连字符与零宽字符；
  这些字符肉眼看不见，却会让翻译 / 检索把一个词当成两个。
* :func:`looks_garbled` —— 选中的文字像不像乱码（字体缺 ToUnicode 映射的 PDF 常见），
  像的话应改用截图识别，而不是把乱码发去翻译。
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "clean_copied_text",
    "has_cjk",
    "is_cjk_char",
    "join_words",
    "looks_garbled",
    "normalize_pdf_text",
]

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


# 排版连字（Alphabetic Presentation Forms）：TeX 排出的论文里 fi / fl 几乎都是连字
_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)
_SOFT_HYPHEN = "­"
# 零宽字符：零宽空格 / 不连字 / 连字、BOM
_INVISIBLE = re.compile("[​‌‍﻿]")


def normalize_pdf_text(text: str) -> str:
    """展开连字，删去软连字符与零宽字符（不动换行和空格）。"""
    return _INVISIBLE.sub("", text.translate(_LIGATURES).replace(_SOFT_HYPHEN, ""))


# 判定乱码的阈值：可疑字符占比，以及「几乎没有字母数字」的符号串（缺字体映射时常被解成
# !"#$%& 之类的 ASCII 符号）至少多长才判定——太短的可能只是一个公式或编号
_GARBLED_RATIO = 0.3
_SYMBOL_SOUP_MIN = 12
_SYMBOL_SOUP_WORDISH = 0.25


def _suspect_char(ch: str) -> bool:
    """控制字符、私用区、未分配码位、代理项与替换符 U+FFFD：正常文字里不会出现。"""
    return ch == "�" or unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cn", "Cs")


def looks_garbled(text: str) -> bool:
    """文字是否像乱码（太短的不判断）。

    两种常见情形：字体字形落在私用区 / 解出替换符（可疑字符占三成以上）；
    或者解成一串 ASCII 符号、几乎没有字母和数字。
    """
    chars = [ch for ch in normalize_pdf_text(text) if not ch.isspace()]
    if len(chars) < 4:
        return False
    if sum(1 for ch in chars if _suspect_char(ch)) / len(chars) > _GARBLED_RATIO:
        return True
    if len(chars) >= _SYMBOL_SOUP_MIN:
        wordish = sum(1 for ch in chars if ch.isalnum())
        return wordish / len(chars) < _SYMBOL_SOUP_WORDISH
    return False


def clean_copied_text(text: str) -> str:
    """合并 PDF 排版换行，保留空行分段。

    * ``inter-\\nnational`` → ``international``（行尾连字符断词，含软连字符）
    * 英文行与行之间换行 → 空格；中文行之间换行 → 直接相连
    * 连续空行视为段落分隔，保留为一个空行
    * 连字展开、零宽字符删除（见 :func:`normalize_pdf_text`）
    """
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(_SOFT_HYPHEN + r"[ \t]*\n", "", normalized)  # 软连字符断行：直接接上
    normalized = normalize_pdf_text(normalized)
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
