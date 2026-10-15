"""预设动作的提示词。

所有需要依据原文的回答都要求以 ``[p.N]`` 标注页码（N 为 1 起的页序），界面会把它变成可点击的跳转；
原文没有依据时要明确说「文中未提及」，不允许编造。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PRESET_ACTIONS",
    "PresetAction",
    "ask_messages",
    "explain_image_messages",
    "explain_messages",
    "parse_indices",
    "parse_questions",
    "preset_by_key",
    "speedread_messages",
    "suggest_messages",
    "summary_messages",
    "tldr_messages",
    "translate_messages",
]

Message = dict[str, Any]

# 多轮问答回传的历史轮数上限（只回传正文，不回传思考内容）
MAX_HISTORY_MESSAGES = 6


def _system(language: str) -> Message:
    return {
        "role": "system",
        "content": (
            "你是 Lumen PDF 阅读器里的论文阅读助手。请用"
            f"{language}回答，语言准确、简洁，使用 Markdown（可用小标题、列表、加粗，数学公式用 $…$）。"
        ),
    }


_CITE_RULES = (
    "规则：\n"
    "1. 只依据下面给出的文档片段回答，片段前的 [p.N] 是它所在的页码；\n"
    "2. 每个要点后用 [p.N] 标注依据的页码（多页写成 [p.3, p.5]），不要编造页码；\n"
    "3. 片段里没有依据的内容，明确说「文中未提及」，不要用常识补充成文中观点。"
)


def translate_messages(text: str, language: str) -> list[Message]:
    return [
        {
            "role": "system",
            "content": (
                f"你是专业的学术翻译。把用户给出的文本翻译成{language}："
                "忠实、通顺，保留术语的准确性（首次出现的专业术语可在括号里保留原文），"
                "保留公式与符号；只输出译文，不要解释。若原文已是目标语言，则润色并只输出结果。"
            ),
        },
        {"role": "user", "content": text},
    ]


def explain_messages(text: str, surrounding: str, language: str) -> list[Message]:
    context = f"\n\n它所在的上下文（供参考）：\n{surrounding}" if surrounding.strip() else ""
    return [
        _system(language),
        {
            "role": "user",
            "content": (
                "请解释论文中的这段内容：先用一两句话说清它在讲什么，再解释其中的术语、符号或推理；"
                f"如果是公式，逐项说明变量含义。\n\n内容：\n{text}{context}"
            ),
        },
    ]


def summary_messages(context: str, language: str, *, truncated: bool) -> list[Message]:
    note = "（文档较长，片段只包含开头与结尾部分）\n" if truncated else ""
    return [
        _system(language),
        {
            "role": "user",
            "content": (
                "请对这篇文档做结构化总结，依次给出以下小节（文档不是论文时可按内容调整）：\n"
                "**研究问题**、**方法**、**主要结果**、**局限**、**可借鉴之处**。\n"
                f"{_CITE_RULES}\n\n{note}文档片段：\n{context}"
            ),
        },
    ]


def tldr_messages(context: str, language: str, *, truncated: bool) -> list[Message]:
    note = "（文档较长，片段只包含开头与结尾部分）\n" if truncated else ""
    return [
        _system(language),
        {
            "role": "user",
            "content": (
                "用不超过 3 句话写出这篇文档的 TL;DR：它要解决什么问题、怎么做、得到什么结论。"
                "只输出这一段话，不要小标题和列表；每句后用 [p.N] 标注依据的页码，"
                "片段里没有依据的内容不要写。\n\n"
                f"{note}文档片段：\n{context}"
            ),
        },
    ]


def speedread_messages(sentences: Sequence[str], count: int) -> list[Message]:
    numbered = "\n".join(f"[{i}] {s}" for i, s in enumerate(sentences, 1))
    return [
        {
            "role": "system",
            "content": "你是论文速读助手，帮读者挑出一页里最值得读的句子。只输出句子编号。",
        },
        {
            "role": "user",
            "content": (
                f"下面是某一页按句编号的原文。挑出速读时最该看的约 {count} 句"
                "（核心论点、方法要点、关键结果、结论；跳过页眉、致谢、引用列表与过渡句）。"
                "只输出编号，用逗号分隔，例如：3, 7, 12。\n\n" + numbered
            ),
        },
    ]


def parse_indices(text: str, total: int) -> list[int]:
    """从「3, 7, 12」式的回答里取出句子编号，返回 0 起、去重、升序的下标。"""
    picked = {int(n) - 1 for n in re.findall(r"\d+", text) if 1 <= int(n) <= total}
    return sorted(picked)


def ask_messages(
    question: str, context: str, language: str, history: list[Message] | None = None
) -> list[Message]:
    messages = [_system(language)]
    for item in (history or [])[-MAX_HISTORY_MESSAGES:]:
        if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append(
        {
            "role": "user",
            "content": f"{_CITE_RULES}\n\n文档片段：\n{context}\n\n问题：{question}",
        }
    )
    return messages


def suggest_messages(context: str, language: str) -> list[Message]:
    return [
        _system(language),
        {
            "role": "user",
            "content": (
                "根据下面的文档片段，提出 5 个读者最值得问的问题（帮助理解核心贡献、方法细节与局限）。"
                "每行一个问题，不要编号，不要其他文字。\n\n文档片段：\n" + context
            ),
        },
    ]


def parse_questions(text: str, limit: int = 5) -> list[str]:
    """从「每行一个问题」的回答里取出问题（容忍编号、项目符号）。"""
    questions: list[str] = []
    for line in text.splitlines():
        item = line.strip().lstrip("-*•·").strip()
        while item[:1].isdigit():
            item = item[1:]
        item = item.lstrip(".、)）:： ").strip()
        if len(item) >= 4 and item not in questions:
            questions.append(item)
    return questions[:limit]


def explain_image_messages(png_base64: str, page_hint: str, language: str) -> list[Message]:
    hint = f"\n\n该区域附近的文字（供参考）：\n{page_hint}" if page_hint.strip() else ""
    return [
        _system(language),
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "这是从论文里框选的一块区域（可能是公式、图或表）。请说明它表达了什么："
                        "公式逐项解释变量与含义；图表说明坐标 / 列的意义、主要趋势与结论。" + hint
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_base64}"}},
            ],
        },
    ]


@dataclass(frozen=True)
class PresetAction:
    """一键动作（借鉴 zotero-gpt 的「命令标签」）：基于全文上下文提出固定问题。"""

    key: str
    title: str
    question: str


PRESET_ACTIONS: tuple[PresetAction, ...] = (
    PresetAction(
        "method", "解释方法", "这篇文档的方法 / 技术路线是什么？请分步骤说明关键设计与理由。"
    ),
    PresetAction(
        "contrib", "列出贡献", "这篇文档的主要贡献有哪些？逐条列出，并说明与已有工作相比新在哪里。"
    ),
    PresetAction(
        "limits",
        "找出局限",
        "这篇文档有哪些局限、假设或潜在问题？作者自己承认的和你从片段中看出的分开列。",
    ),
    PresetAction(
        "review",
        "审稿意见",
        "以同行评审的角度给出审稿意见：总体评价、主要优点、主要问题与修改建议。",
    ),
    PresetAction("terms", "提炼术语", "提炼文中的关键术语与缩写，逐条给出简短释义。"),
)


def preset_by_key(key: str) -> PresetAction | None:
    return next((a for a in PRESET_ACTIONS if a.key == key), None)
