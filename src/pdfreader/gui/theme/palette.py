"""Lumen Pure 设计令牌（简约学术工作台：冷调中性纸感 + 森玉绿 Forest Jade 主色）。

所有界面颜色、字号、圆角、阴影均从此处读取，保证浅色 / 深色 / 高对比度三套主题一致。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    """一套主题色板（Lumen Pure 设计规范）。"""

    name: str  # "light" | "dark" | "contrast"

    # 表面层级：冷调中性灰白 / 石板暗色，突出 PDF 纸面内容本身
    window: str
    surface: str
    surface_hover: str
    surface_active: str
    canvas: str
    popover: str
    card: str

    # 边框（1px 精细分割线）
    border: str
    border_strong: str

    # 文字层级（全部满足 WCAG AA 对比度 >= 4.5:1）
    text: str
    text_secondary: str
    text_muted: str
    text_disabled: str

    # 品牌强调色（森玉绿 Forest Jade：克制、护眼、学术感）
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_text: str
    on_accent: str

    # 品牌徽标渐变
    brand_from: str
    brand_to: str

    # 图标
    icon: str
    icon_active: str

    # 语义色
    danger: str
    danger_soft: str
    warning: str
    success: str
    mark: str

    # 画布纸张与阴影
    page_border: str
    shadow: str  # QColor 可识别的 #AARRGGBB
    soft_shadow: str

    # 文献库工作台（Lumen Pure 轻量中性侧栏，告别厚重海报风）
    nav: str
    nav_active: str
    nav_text: str
    nav_muted: str
    nav_bright: str
    nav_icon_active: str
    import_button: str
    import_button_hover: str
    hero: str
    hero_ink: str
    hero_button: str
    hero_button_hover: str
    insight: str
    insight_ink: str
    cream: str
    # 四种低饱和学术纸张封面底色与墨色：(背景, 墨色)
    covers: tuple[tuple[str, str], tuple[str, str], tuple[str, str], tuple[str, str]]

    @property
    def is_dark(self) -> bool:
        return self.name in ("dark", "contrast")


LIGHT = Palette(
    name="light",
    window="#F6F7F9",
    surface="#FFFFFF",
    surface_hover="#F0F2F6",
    surface_active="#E6E9EF",
    canvas="#EDEFF3",
    popover="#FFFFFF",
    card="#FFFFFF",
    border="#E4E7EC",
    border_strong="#CFD4DC",
    text="#18181B",
    text_secondary="#474B54",
    text_muted="#626773",
    text_disabled="#A0A6B2",
    accent="#17795E",
    accent_hover="#12634C",
    accent_pressed="#0E4F3D",
    accent_soft="#E6F4F0",
    accent_text="#136850",
    on_accent="#FFFFFF",
    brand_from="#17795E",
    brand_to="#115E48",
    icon="#3F434C",
    icon_active="#136850",
    danger="#DC2626",
    danger_soft="#FEE2E2",
    warning="#D97706",
    success="#16A34A",
    mark="#FCD34D",
    page_border="#DDE1E8",
    shadow="#14101828",
    soft_shadow="#1E101828",
    nav="#F8F9FB",
    nav_active="#E6F4F0",
    nav_text="#474B54",
    nav_muted="#626773",
    nav_bright="#18181B",
    nav_icon_active="#17795E",
    import_button="#17795E",
    import_button_hover="#12634C",
    hero="#FFFFFF",
    hero_ink="#18181B",
    hero_button="#17795E",
    hero_button_hover="#12634C",
    insight="#F2F8F6",
    insight_ink="#136850",
    cream="#EFF2F6",
    covers=(
        ("#EDF6F3", "#185946"),
        ("#EEF3FA", "#284870"),
        ("#F5F3EE", "#58493A"),
        ("#F2EFF8", "#483C66"),
    ),
)

DARK = Palette(
    name="dark",
    window="#111317",
    surface="#171A21",
    surface_hover="#212630",
    surface_active="#2A303C",
    canvas="#0C0E12",
    popover="#1C2029",
    card="#1A1E26",
    border="#262B36",
    border_strong="#363D4D",
    text="#F1F3F5",
    text_secondary="#B4BCCB",
    text_muted="#8C95A6",
    text_disabled="#525968",
    accent="#2BB68E",
    accent_hover="#38C89E",
    accent_pressed="#1F9A76",
    accent_soft="#152E27",
    accent_text="#4AD2AA",
    on_accent="#081712",
    brand_from="#24A17D",
    brand_to="#17795E",
    icon="#A6AEBE",
    icon_active="#4AD2AA",
    danger="#F87171",
    danger_soft="#3B1D22",
    warning="#FBBF24",
    success="#4ADE80",
    mark="#B45309",
    page_border="#262B36",
    shadow="#66000000",
    soft_shadow="#7A000000",
    nav="#14171D",
    nav_active="#17322A",
    nav_text="#B4BCCB",
    nav_muted="#8C95A6",
    nav_bright="#F1F3F5",
    nav_icon_active="#4AD2AA",
    import_button="#24A17D",
    import_button_hover="#2BB68E",
    hero="#171A21",
    hero_ink="#F1F3F5",
    hero_button="#24A17D",
    hero_button_hover="#2BB68E",
    insight="#152622",
    insight_ink="#4AD2AA",
    cream="#1E232D",
    covers=(
        ("#182924", "#9AD8C5"),
        ("#1A2433", "#A4C2E8"),
        ("#26221D", "#D6C4AE"),
        ("#221E2E", "#C4B5E6"),
    ),
)

CONTRAST = Palette(
    name="contrast",
    window="#000000",
    surface="#0A0A0A",
    surface_hover="#1F1F1F",
    surface_active="#2E2E2E",
    canvas="#000000",
    popover="#0A0A0A",
    card="#0A0A0A",
    border="#FFFFFF",
    border_strong="#FFD60A",
    text="#FFFFFF",
    text_secondary="#FFFFFF",
    text_muted="#FFE680",
    text_disabled="#9E9E9E",
    accent="#FFD60A",
    accent_hover="#FFE34D",
    accent_pressed="#E6BE00",
    accent_soft="#332B00",
    accent_text="#FFD60A",
    on_accent="#000000",
    brand_from="#FFD60A",
    brand_to="#FFFFFF",
    icon="#FFFFFF",
    icon_active="#FFD60A",
    danger="#FF6B6B",
    danger_soft="#3D0000",
    warning="#FFD60A",
    success="#52FF9A",
    mark="#3D3200",
    page_border="#FFFFFF",
    shadow="#CC000000",
    soft_shadow="#CC000000",
    nav="#000000",
    nav_active="#2E2E2E",
    nav_text="#FFFFFF",
    nav_muted="#FFE680",
    nav_bright="#FFFFFF",
    nav_icon_active="#FFD60A",
    import_button="#FFD60A",
    import_button_hover="#FFE34D",
    hero="#0A0A0A",
    hero_ink="#FFFFFF",
    hero_button="#FFD60A",
    hero_button_hover="#FFE34D",
    insight="#0A0A0A",
    insight_ink="#FFD60A",
    cream="#1F1F1F",
    covers=(
        ("#121212", "#FFFFFF"),
        ("#121212", "#FFD60A"),
        ("#121212", "#80E5FF"),
        ("#121212", "#FFB86C"),
    ),
)


@dataclass(frozen=True)
class Tokens:
    """与主题无关的尺寸与动效令牌（Lumen Pure 紧凑比例）。"""

    font_family: str = '"Microsoft YaHei UI", "PingFang SC", "Segoe UI", system-ui, sans-serif'
    mono_family: str = '"Cascadia Mono", "Consolas", "SF Mono", ui-monospace, monospace'
    serif_family: str = '"Georgia", "Times New Roman", "Songti SC", "SimSun", serif'

    font_size_xs: int = 11
    font_size_sm: int = 12
    font_size_base: int = 13
    font_size_lg: int = 15

    radius_sm: int = 6
    radius_md: int = 8
    radius_lg: int = 10
    radius_xl: int = 12

    header_height: int = 40
    ribbon_height: int = 40
    status_height: int = 28

    # 动效时长（ms）——短促轻盈的阻尼曲线（OutExpo），不拖泥带水
    anim_fast: int = 160
    anim_drop: int = 220
    anim_up: int = 280
    anim_sidebar: int = 280
    anim_pulse: int = 900
    anim_scroll: int = 300


TOKENS = Tokens()


def palette_for(name: str) -> Palette:
    """按名称返回配色板（未知名称回退浅色）。"""
    if name == "dark":
        return DARK
    if name == "contrast":
        return CONTRAST
    return LIGHT
