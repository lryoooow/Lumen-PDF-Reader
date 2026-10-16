"""设计令牌与配色板（Lumen 视觉，2026-10 第二版设计稿：暖纸底 + 赤陶强调色）。

色值取自设计稿 ``UI/src/index.css``（暖灰中性色 + 赤陶 #c96a49 强调色 + 矿物色封面）。设计稿里的
半透明叠色（如 ``bg-black/[.045]``、``bg-white/[.06]``）在这里预先与底色合成为不透明色，
因为 Qt 样式表对 rgba 叠加的表现与浏览器不一致。

所有 GUI 视觉常量的唯一来源——控件从不硬编码颜色，一律取自这里。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DARK", "LIGHT", "TOKENS", "Palette", "Tokens", "palette_for"]


@dataclass(frozen=True, slots=True)
class Palette:
    """一套完整的语义化配色（以用途命名，浅 / 深色各自赋值）。"""

    name: str  # "light" | "dark"

    # 背景层次
    window: str  # 应用主背景（暖灰纸感）
    surface: str  # 标题栏 / 功能区 / 侧边栏 / 状态栏
    surface_hover: str  # 可交互表面悬停
    surface_active: str  # 按下 / 分段控件滑块
    canvas: str  # 阅读区画布（与主背景一致，页面靠阴影浮起）
    popover: str  # 下拉菜单 / 浮层卡片
    card: str  # 批注卡片

    # 描边
    border: str
    border_strong: str

    # 文本
    text: str
    text_secondary: str
    text_muted: str

    # 强调色（赤陶 terracotta）
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str  # 选中 / 勾选态的淡色底
    accent_text: str  # 强调色文字（选中按钮、链接）
    on_accent: str

    # 品牌色（logo 方块：赤陶，略带纵深）
    brand_from: str
    brand_to: str

    # 语义色
    danger: str
    danger_soft: str
    success: str  # 状态消息前的小圆点
    mark: str  # 搜索命中片段底色（amber）

    # 图标
    icon: str
    icon_active: str

    # 阴影
    shadow: str

    # ---- 文献库（Studio）：墨绿导航栏 + 矿物色封面 + 主推卡片 ----
    nav: str = "#282D29"
    nav_text: str = "#C2C7BD"
    nav_muted: str = "#7D877B"
    nav_active: str = "#3B423A"
    nav_bright: str = "#EFF1E7"  # 选中项文字 / 品牌字
    nav_icon_active: str = "#DCA27B"
    import_button: str = "#CF7855"
    import_button_hover: str = "#DF8864"
    hero: str = "#EAECE2"  # 「从上次的思考继续」卡片
    hero_ink: str = "#3B493B"
    hero_button: str = "#3E4B3B"
    hero_button_hover: str = "#53634F"
    insight: str = "#EEE6DA"  # 「AI 助读」卡片
    insight_ink: str = "#9E6B46"
    cream: str = "#EDECE4"  # 视图切换选中底
    soft_shadow: str = "rgba(48, 58, 48, 0.07)"
    # 封面（底色, 墨色）：sage / sand / rose / blue，按文件路径稳定取一种
    covers: tuple[tuple[str, str], ...] = (
        ("#DCE4D7", "#475C48"),
        ("#ECE3D2", "#746047"),
        ("#EADBD4", "#80594C"),
        ("#DCE3E6", "#49616B"),
    )

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


@dataclass(frozen=True, slots=True)
class Tokens:
    """与主题无关的尺寸 / 字体 / 动效令牌。"""

    radius_sm: int = 6
    radius: int = 8
    radius_lg: int = 12
    radius_xl: int = 16
    gap_sm: int = 6
    gap: int = 10
    gap_lg: int = 16
    font_family: str = (
        '"Inter", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", '
        '"Microsoft YaHei", "PingFang SC", sans-serif'
    )
    # 正文衬线体（标题、批注引文、搜索片段）：设计稿为 Source Serif 4 + Noto Serif SC
    serif_family: str = (
        '"Source Serif 4", "Noto Serif SC", "Source Han Serif SC", "Songti SC", '
        '"SimSun", "Georgia", serif'
    )
    font_pt: int = 10
    # 动效（毫秒）：与设计稿 CSS 一致；缓动统一用 OutExpo（≈ cubic-bezier(.22,1,.36,1)）
    anim_fast: int = 200
    anim_drop: int = 320
    anim_up: int = 500
    anim_sidebar: int = 500
    anim_scroll: int = 380
    anim_pulse: int = 900


TOKENS = Tokens()


# ----------------------------------------------------------------------------
# 浅色：暖纸底 #f6f5f1 + 米白面板 + 暖灰文字 + 赤陶强调
# ----------------------------------------------------------------------------
LIGHT = Palette(
    name="light",
    window="#F6F5F1",
    surface="#FCFBF8",
    surface_hover="#EFEDE7",
    surface_active="#FFFFFF",
    canvas="#F6F5F1",
    popover="#FFFFFF",
    card="#FFFFFF",
    border="#E8E5DE",
    border_strong="#D6D2C9",
    text="#30312E",
    text_secondary="#686A63",
    text_muted="#92938A",
    accent="#C96A49",
    accent_hover="#B75B3C",
    accent_pressed="#A44F33",
    accent_soft="#F5E9DF",
    accent_text="#B75B3C",
    on_accent="#FFFFFF",
    brand_from="#C96A49",
    brand_to="#B75B3C",
    danger="#F43F5E",
    danger_soft="#FDE8EC",
    success="#10B981",
    mark="#FCD34D",
    icon="#3F3F46",
    icon_active="#B75B3C",
    shadow="rgba(0, 0, 0, 0.18)",
)


# ----------------------------------------------------------------------------
# 深色：近黑底 #0f1115 + 面板 #16181d + 浮层 #1d2027
# ----------------------------------------------------------------------------
DARK = Palette(
    name="dark",
    window="#0F1115",
    surface="#16181D",
    surface_hover="#202227",
    surface_active="#2A2C31",
    canvas="#0F1115",
    popover="#1D2027",
    card="#1B1D22",
    border="#24262B",
    border_strong="#33363D",
    text="#E4E4E7",
    text_secondary="#A1A1AA",
    text_muted="#71717A",
    accent="#C96A49",
    accent_hover="#E28D69",
    accent_pressed="#B75B3C",
    accent_soft="#3B2E28",
    accent_text="#E8A17E",
    on_accent="#FFFFFF",
    brand_from="#C96A49",
    brand_to="#B75B3C",
    danger="#FB7185",
    danger_soft="#3A1F27",
    success="#10B981",
    mark="#B45309",
    icon="#D4D4D8",
    icon_active="#E8A17E",
    shadow="rgba(0, 0, 0, 0.55)",
    hero="#303A30",
    hero_ink="#D8E2D2",
    hero_button="#4A5A46",
    hero_button_hover="#5B6D56",
    insight="#3C352D",
    insight_ink="#E0A47A",
    cream="#33362E",
    soft_shadow="rgba(0, 0, 0, 0.35)",
)


def palette_for(name: str) -> Palette:
    """按名称取配色板（未知名回退浅色）。"""
    return DARK if name == "dark" else LIGHT
