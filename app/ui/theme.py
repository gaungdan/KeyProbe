"""界面主题：字体、配色与间距的单一来源。

外壳是纯黑，灰阶仍分三层（外壳 < 面板 < 读数窗），层差靠极窄的亮度台阶表达，
面板边界不需要描边就能看出来。操作按钮改为全部实心：纯黑底上，
描边按钮的对比度不足以让人第一眼找到「该点哪里」。主次关系改用蓝色深浅区分——
主操作用电蓝 SIGNAL，次级主操作用亮天蓝 ACCENT，辅助操作用中性实心 NEUTRAL_FILL。

字族在运行时挑选第一个系统真实安装的：原代码硬编码 ``("Helvetica", 14)``，
但 Windows 上并没有 Helvetica 字族，Tk 会静默回退到默认字体，
中文渲染发虚、字重不一致。
"""

from __future__ import annotations

import tkinter.font as tkfont

# 候选字族按优先级排列，取第一个系统真实安装的
_UI_CANDIDATES = ("Microsoft YaHei UI", "Segoe UI", "PingFang SC", "Arial")
_MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New")

_ui_family: str | None = None
_mono_family: str | None = None


def _pick(candidates: tuple[str, ...], fallback: str) -> str:
    try:
        available = set(tkfont.families())
    except Exception:
        return fallback
    for name in candidates:
        if name in available:
            return name
    return fallback


def ui_family() -> str:
    """正文字族（需在 Tk root 创建后调用）。"""

    global _ui_family
    if _ui_family is None:
        _ui_family = _pick(_UI_CANDIDATES, "TkDefaultFont")
    return _ui_family


def mono_family() -> str:
    """等宽字族，用于读数、标签、cURL、日志与体检明细。"""

    global _mono_family
    if _mono_family is None:
        _mono_family = _pick(_MONO_CANDIDATES, "TkFixedFont")
    return _mono_family


def font(size: int = 13, weight: str = "normal") -> tuple:
    """正文字体，仅用于说明性文字与按钮。"""

    return (ui_family(), size, weight) if weight != "normal" else (ui_family(), size)


def mono(size: int = 12, weight: str = "normal") -> tuple:
    """等宽字体，用于一切数据与字段标签。"""

    return (mono_family(), size, weight) if weight != "normal" else (mono_family(), size)


def caps(text: str) -> str:
    """把短标签拍成加宽字距的大写眉标。

    Tk 没有 letter-spacing，用空格手动撑开是唯一手段。只用于 3～8 字符的
    分区眉标，长文本会被撑得无法阅读。
    """

    return " ".join(text.upper())


# ---------- 间距刻度 ----------
# 4 的倍数，避免各面板各写一套 padx/pady 后边缘对不齐
GAP_XS = 4
GAP_SM = 8
GAP_MD = 12
GAP_LG = 18
GAP_XL = 26

# 控件统一高度：控制面板上的所有可操作元件应当等高
CONTROL_HEIGHT = 36
# 操作按钮比输入控件更高一档，在一片深色里先被眼睛抓到
BUTTON_HEIGHT = 42
BUTTON_RADIUS = 10


# ---------- 灰阶三层 ----------
# 外壳是纯黑，面板与读数窗只靠极窄的亮度差浮在上面。
# 纯黑底让蓝色实心按钮拿到全窗口最大的亮度差，周围没有任何竞争。
INK = "#000000"      # 仪器外壳 / 窗口底
PANEL = "#0D131B"    # 控制面板、卡片
WELL = "#05080C"     # 下沉读数窗：cURL、日志、体检明细
EDGE = "#1F2A38"     # 发丝分隔线与边框

# ---------- 文字 ----------
TEXT_PRIMARY = "#EEF4FB"
TEXT_MUTED = "#9FB0C6"
TEXT_FAINT = "#6A7A8D"

# ---------- 信号色 ----------
# 主操作色：高饱和电蓝，压在近黑底上亮度差最大。
SIGNAL = "#2F7DFF"
SIGNAL_HOVER = "#4A92FF"
SIGNAL_DIM = "#1C3A6B"  # 焦点环与低强度描边

# 次级操作色：同在蓝域但更亮更偏青，实心也不会被误认作主操作。
ACCENT = "#38BDF8"
ACCENT_HOVER = "#5CCCFB"

# ---------- 语义色 ----------
# INFO 与主色同色域，为避免和按钮抢注意力，改用偏紫的蓝
OK = "#3DDC84"
WARN = "#FFC53D"
ERROR = "#FF6B6B"
INFO = "#A5B8FF"
IDLE = "#6A7A8D"

# ---------- 控件表面 ----------
SURFACE_INPUT = "#141B25"
SURFACE_DIVIDER = EDGE

CONTROL = SURFACE_INPUT
CONTROL_HOVER = "#1E2733"
DROPDOWN_BG = PANEL
DROPDOWN_HOVER = "#1E2733"
NEUTRAL_HOVER = "#25313F"

# 三级按钮的实心底：比输入框亮一档，仍然明显退在两个蓝色按钮之后
NEUTRAL_FILL = "#1C2533"

CODE_TEXT = "#FFFFFF"

# 实心按钮上的文字：深蓝底用白字，亮天蓝底用外壳色，两者都是最大对比方向
ON_SIGNAL = "#FFFFFF"
ON_ACCENT = INK
