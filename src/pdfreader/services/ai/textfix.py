"""AI 回答的显示前整理（纯文本，不依赖界面）。

模型输出常见三类「符号露馅」：

* LaTeX：``$O(n^2)$``、``\\(...\\)``、``$$...$$`` 原样显示——转成 Unicode（O(n²)、√d、α…）；
* 中文里夹半角标点：``方法,效果``、``"注意力"``——在中文上下文里换成全角（，“”（）：）；
* 中文加粗失效：``**“层级注意力”**`` 这类以标点开头 / 结尾的加粗，CommonMark 的定界规则不认，
  ``**`` 原样露出——渲染前改写成 ``<b>…</b>``（:func:`bold_to_html`）。

代码块与行内代码原样保留；只做确定安全的替换，拿不准的保持原样。
"""

from __future__ import annotations

import re
from collections.abc import Callable

__all__ = ["DISPLAY_MATH_MARK", "bold_to_html", "tex_to_unicode", "tidy_markdown"]

# 行间公式段落开头的不可见标记（U+2060 WORD JOINER），界面据此把这一段居中
DISPLAY_MATH_MARK = "\u2060"

# ---------------------------------------------------------------- 代码保护
_CODE = re.compile(r"(```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)|`[^`\n]+`)", re.S)


def _outside_code(text: str, fix: Callable[[str], str]) -> str:
    """只对代码块 / 行内代码以外的部分应用 ``fix``。"""
    parts = _CODE.split(text)
    return "".join(part if i % 2 else fix(part) for i, part in enumerate(parts))


# ---------------------------------------------------------------- LaTeX → Unicode
# fmt: off
_SYMBOLS = {
    # 希腊字母
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # 运算与关系
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆",
    "circ": "∘", "oplus": "⊕", "otimes": "⊗", "odot": "⊙",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈",
    "sim": "∼", "simeq": "≃", "equiv": "≡", "propto": "∝", "ll": "≪", "gg": "≫",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "supset": "⊃", "cup": "∪",
    "cap": "∩", "emptyset": "∅", "varnothing": "∅", "forall": "∀", "exists": "∃",
    "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨",
    # 箭头
    "to": "→", "rightarrow": "→", "leftarrow": "←", "gets": "←", "leftrightarrow": "↔",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓",
    # 大型算子与其他
    "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮", "partial": "∂", "nabla": "∇",
    "infty": "∞", "ell": "ℓ", "prime": "′", "top": "ᵀ", "dagger": "†", "hbar": "ħ",
    "ldots": "…", "cdots": "⋯", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "mid": "|", "vert": "|", "Vert": "‖", "|": "‖", "{": "{", "}": "}", "%": "%", "_": "_",
    "&": "&", "#": "#", "$": "$",
    "quad": " ", "qquad": "  ", ",": " ", ";": " ", ":": " ", "!": "", " ": " ",
}
# 函数名照写（\log → log）
_NAMES = {
    "log", "ln", "exp", "sin", "cos", "tan", "tanh", "sigmoid", "max", "min", "arg", "argmax",
    "argmin", "lim", "sup", "inf", "det", "dim", "softmax", "Pr", "var", "Var", "Cov", "E",
}
_DROP = {"left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "textstyle", "limits"}
_STYLE = {"text", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "boldsymbol", "operatorname",
          "textbf", "textit", "mathcal", "bm", "emph"}
_BB = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "E": "𝔼", "P": "ℙ"}
_ACCENTS = {"hat": "\u0302", "widehat": "\u0302", "bar": "\u0304", "overline": "\u0304",
            "tilde": "\u0303", "widetilde": "\u0303", "vec": "\u20d7", "dot": "\u0307",
            "ddot": "\u0308"}

_SUP = dict(zip("0123456789+-=()niabcdefghjklmoprstuvwxyzABDEGHIJKLMNOPRTUVW",
                "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱᵃᵇᶜᵈᵉᶠᵍʰʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ", strict=True))
_SUB = dict(zip("0123456789+-=()aehijklmnoprstuvx",
                "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ", strict=True))
_SUP.update({"′": "′", "ᵀ": "ᵀ", "∗": "*", "*": "*"})  # A^{\prime}、A^\top、x^*
# fmt: on


def _group(s: str, i: int) -> tuple[str, int]:
    """从 ``s[i]`` 起读一个参数：``{…}``（配平）、``\\cmd`` 或单个字符。"""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1 : j], j + 1
            j += 1
        return s[i + 1 :], len(s)
    if s[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", s[i:])
        if m:
            return m.group(0), i + m.end()
    return s[i], i + 1


_OPERATORS = set(" +-−=×·/÷,<>≤≥≈±")


def _wrap(term: str) -> str:
    """分式 / 根号里是多项（含运算符）时加括号：a+b/c → (a+b)/c；√dₖ 不加。"""
    return f"({term})" if any(ch in _OPERATORS for ch in term) else term


def _script(body: str, table: dict[str, str], mark: str) -> str:
    if body and all(ch in table for ch in body):
        return "".join(table[ch] for ch in body)
    return f"{mark}({body})" if len(body) > 1 else f"{mark}{body}"


def tex_to_unicode(expr: str) -> str:
    """把一段 LaTeX 数学转成可读的 Unicode（常见写法；认不出的命令去掉反斜杠照写）。"""
    out: list[str] = []
    s, i = expr, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            m = re.match(r"\\([A-Za-z]+|.)", s[i:])
            if not m:
                i += 1
                continue
            name, i = m.group(1), i + m.end()
            if name in _SYMBOLS:
                out.append(_SYMBOLS[name])
            elif name in _NAMES:
                # 函数名与前面的变量隔开：n\log n → n log n
                gap = " " if out and out[-1][-1:].isalnum() else ""
                out.append(gap + name)
            elif name in _DROP:
                continue
            elif name in _STYLE:
                arg, i = _group(s, i)
                out.append(tex_to_unicode(arg))
            elif name == "mathbb":
                arg, i = _group(s, i)
                out.append("".join(_BB.get(c, c) for c in arg))
            elif name in _ACCENTS:
                arg, i = _group(s, i)
                inner = tex_to_unicode(arg)
                out.append(inner + _ACCENTS[name] if len(inner) == 1 else inner)
            elif name in ("frac", "dfrac", "tfrac"):
                num, i = _group(s, i)
                den, i = _group(s, i)
                a, b = tex_to_unicode(num), tex_to_unicode(den)
                out.append(f"{_wrap(a)}/{_wrap(b)}")
            elif name == "sqrt":
                index = ""
                if i < len(s) and s[i] == "[":
                    end = s.find("]", i)
                    if end > 0:
                        index, i = s[i + 1 : end], end + 1
                arg, i = _group(s, i)
                inner = tex_to_unicode(arg)
                root = {"3": "∛", "4": "∜"}.get(index, "√")
                out.append(root + _wrap(inner))
            else:
                out.append(name)
        elif ch in "^_":
            arg, i = _group(s, i + 1)
            body = tex_to_unicode(arg).replace(" ", "")
            out.append(_script(body, _SUP if ch == "^" else _SUB, ch))
        elif ch in "{}":
            i += 1
        elif ch == "~":
            out.append(" ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# 行间公式：$$…$$、\[…\]（可跨行）；行内：\(…\)、$…$（$ 后不是空白，结尾 $ 前不是空白、后面不是数字）
_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
_BLANK_LINES = re.compile(r"\n{3,}")
_INLINE_PAREN = re.compile(r"\\\((.+?)\\\)")
_INLINE_DOLLAR = re.compile(r"(?<![\\$\w])\$(?=[^\s$])([^$\n]+?)(?<=[^\s\\$])\$(?![\d$])")


def _convert_math(text: str, mark: str = "") -> str:
    def display(m: re.Match[str]) -> str:
        body = tex_to_unicode(m.group(1) or m.group(2) or "")
        return f"\n\n{mark}{body}\n\n" if body else ""

    text = _BLANK_LINES.sub("\n\n", _DISPLAY.sub(display, text))
    text = _INLINE_PAREN.sub(lambda m: tex_to_unicode(m.group(1)), text)
    return _INLINE_DOLLAR.sub(lambda m: tex_to_unicode(m.group(1)), text)


# ---------------------------------------------------------------- 中文标点
_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_CTX = f"[{_CJK}\u3000-\u303f\uff00-\uffef“”‘’《》]"
_HALF_TO_FULL = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}
# 半角标点：前面是中文（或中文标点，中间可隔着加粗的 **），后面不是数字 / 「//」（网址），顺带吃掉后面的空格
_PUNCT_AFTER_CJK = re.compile(rf"(?<={_CJK_CTX})(\*{{1,2}})?([,;:!?])(?!//)(?!\d) ?")
# 前面是西文、后面紧跟中文：「attention,但是」
_PUNCT_BEFORE_CJK = re.compile(rf"(?<=[A-Za-z\)\]])([,;!?]) ?(?=[{_CJK}])")
# 句点：前面是中文，后面是中文 / 空白 / 行尾（「汇总.见」）；「...」另算省略号
_PERIOD = re.compile(rf"(?<=[{_CJK}])\.(?!\.)(?=[{_CJK}]|\s|$)")
_ELLIPSIS = re.compile(rf"(?<={_CJK_CTX})(?:\.{{3,}}|…)(?!…)")
_DQUOTE = re.compile(rf'"([^"\n]*[{_CJK}][^"\n]*)"')
_PARENS = re.compile(rf"(?<={_CJK_CTX})\(([^()\n]*)\)|\(([^()\n]*[{_CJK}][^()\n]*)\)")


def _fix_line(line: str) -> str:
    # 表格分隔行、引用块标记等不含中文的行直接跳过
    if not re.search(f"[{_CJK}]", line):
        return line
    line = _DQUOTE.sub(lambda m: f"“{m.group(1)}”", line)
    line = _PARENS.sub(
        lambda m: f"（{m.group(1) if m.group(1) is not None else m.group(2)}）", line
    )
    line = _ELLIPSIS.sub("……", line)
    line = _PERIOD.sub("。", line)
    line = _PUNCT_AFTER_CJK.sub(lambda m: (m.group(1) or "") + _HALF_TO_FULL[m.group(2)], line)
    return _PUNCT_BEFORE_CJK.sub(lambda m: _HALF_TO_FULL[m.group(1)], line)


_LINK = re.compile(r"(\]\([^)\s]*\)|https?://\S+)")


def _fix_punct(text: str) -> str:
    def fix(segment: str) -> str:
        # 链接目标与网址原样保留
        parts = _LINK.split(segment)
        out: list[str] = []
        for i, part in enumerate(parts):
            if i % 2:
                out.append(part)
            elif i == 0 or not part:
                out.append(_fix_line(part))
            else:
                # 带上前一段（链接）的末字符作上下文：「](cite:2),以及」→「，」
                out.append(_fix_line(parts[i - 1][-1] + part)[1:])
        return "".join(out)

    return "\n".join(fix(line) for line in text.split("\n"))


# ---------------------------------------------------------------- 入口
def tidy_markdown(text: str, *, mark_display: bool = False) -> str:
    """显示 / 复制 / 存批注前整理回答：LaTeX 转 Unicode、中文语境下的半角标点换成全角。

    ``mark_display`` 时行间公式段落以 :data:`DISPLAY_MATH_MARK` 开头（只给界面排版用）。
    """
    if not text:
        return text
    mark = DISPLAY_MATH_MARK if mark_display else ""
    return _outside_code(text, lambda seg: _fix_punct(_convert_math(seg, mark)))


_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")


def bold_to_html(text: str) -> str:
    """``**…**`` → ``<b>…</b>``：中文里以标点开头 / 结尾的加粗也能正确显示（代码内不动）。"""
    return _outside_code(text, lambda seg: _BOLD.sub(r"<b>\1</b>", seg))
