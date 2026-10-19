"""AI 回答的显示前整理：LaTeX → Unicode、中文语境下的全角标点、中文加粗（纯逻辑）。"""

from __future__ import annotations

import pytest

from pdfreader.services.ai.textfix import (
    DISPLAY_MATH_MARK,
    bold_to_html,
    tex_to_unicode,
    tidy_markdown,
)

B = chr(92)  # 反斜杠，避免测试源码里满是转义


@pytest.mark.parametrize(
    ("tex", "expected"),
    [
        ("O(n^2)", "O(n²)"),
        (f"O(n{B}log n)", "O(n log n)"),
        (
            f"{B}mathrm{{softmax}}{B}left({B}frac{{QK^T}}{{{B}sqrt{{d_k}}}}{B}right)V",
            "softmax(QKᵀ/√dₖ)V",
        ),
        # 帽子是组合附加符（y + U+0302）
        (f"{B}hat{{y}} = {B}sum_{{i=1}}^{{n}} {B}alpha_i x_i", f"y{chr(0x302)} = ∑ᵢ₌₁ⁿ αᵢ xᵢ"),
        (f"W {B}in {B}mathbb{{R}}^{{d {B}times d}}", "W ∈ ℝ^(d×d)"),
        ("A^{-1}", "A⁻¹"),
        (f"x^{B}top", "xᵀ"),
        (f"{B}frac{{a+b}}{{2}}", "(a+b)/2"),
        (f"{B}sqrt[3]{{x}}", "∛x"),
        (f"a {B}leq b {B}neq c {B}to {B}infty", "a ≤ b ≠ c → ∞"),
        ("W_q", "W_q"),  # 没有对应的下标字符：照写
        (f"{B}text{{loss}}_{{total}}", "lossₜₒₜₐₗ"),
        (f"{B}mathbf{{W}}_{{qk}}", "W_(qk)"),  # q 没有下标字符：括起来
    ],
)
def test_tex_to_unicode(tex: str, expected: str) -> None:
    assert tex_to_unicode(tex) == expected


def test_math_delimiters() -> None:
    assert tidy_markdown("复杂度是 $O(n^2)$。") == "复杂度是 O(n²)。"
    assert tidy_markdown(f"而 {B}(O(n){B}) 更好") == "而 O(n) 更好"
    display = tidy_markdown("公式：\n\n$$E = mc^2$$\n\n结束")
    assert display == "公式：\n\nE = mc²\n\n结束"
    assert tidy_markdown(f"{B}[ x^2 {B}]").strip() == "x²"
    money = "价格是 $5 和 $8 两档"
    assert tidy_markdown(money) == money  # 金额不是公式


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("方法,效果很好!", "方法，效果很好！"),
        ("**方法**:先分块,再汇总.见 [p.2]", "**方法**：先分块，再汇总。见 [p.2]"),
        ('这里叫"自注意力"(self-attention)机制...', "这里叫“自注意力”（self-attention）机制……"),
        ("注意力机制很重要;但是代价高?", "注意力机制很重要；但是代价高？"),
        ("使用 attention,但是", "使用 attention，但是"),
        ("第1步:计算 10:30, 2,000 个", "第1步：计算 10:30, 2,000 个"),  # 数字里的标点不动
        ("版本 1.2 与 v3.0 不变。", "版本 1.2 与 v3.0 不变。"),
        ("In English, nothing: changes (ok)?", "In English, nothing: changes (ok)?"),
        (
            "见 [第二节](cite:2),以及 https://a.com/x?a=1,2",
            "见 [第二节](cite:2)，以及 https://a.com/x?a=1,2",
        ),
    ],
)
def test_cjk_punctuation(raw: str, expected: str) -> None:
    assert tidy_markdown(raw) == expected


def test_code_untouched() -> None:
    text = '使用 `a,b:$x^2$` 实现,好\n```python\nx = "中文",1  # $y^2$\n```\n结束,好'
    out = tidy_markdown(text)
    assert "`a,b:$x^2$`" in out and 'x = "中文",1  # $y^2$' in out
    assert out.startswith("使用 `a,b:$x^2$` 实现，好") and out.endswith("结束，好")
    assert tidy_markdown("") == ""


def test_bold_to_html() -> None:
    assert bold_to_html("提出了**“层级注意力”**机制") == "提出了<b>“层级注意力”</b>机制"
    assert bold_to_html("**《Attention》**一文") == "<b>《Attention》</b>一文"
    assert bold_to_html("a ** b ** c") == "a ** b ** c"  # 两侧有空格不是加粗
    assert bold_to_html("`**x**` 与 **y**") == "`**x**` 与 <b>y</b>"


def test_display_math_mark() -> None:
    text = "公式：\n\n$$E = mc^2$$\n\n结束"
    assert DISPLAY_MATH_MARK not in tidy_markdown(text)  # 复制 / 存批注不带标记
    assert f"\n\n{DISPLAY_MATH_MARK}E = mc²\n\n" in tidy_markdown(text, mark_display=True)
