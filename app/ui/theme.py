"""界面主题：字体、配色与间距的单一来源。

外壳是纯黑，灰阶仍分三层（外壳 < 面板 < 读数窗），层差靠亮度台阶表达，
面板边界不需要描边就能看出来。

动作按钮分三级，靠「填充色 + 描边」两重线索区分：主操作用深一档的电蓝实心
（全窗只应出现一个），次级主操作用中性实心加 1px 描边，辅助操作用纯描边。
早先用「更亮的青蓝」区分次级，结果青蓝在黑底上的亮度高于主蓝，
主次关系被视觉直接读反了——亮度差永远比色相差更强势。
三档之间不再用高度差做第二重线索：并排时上下边缘错开 2px，读到的是没对齐。

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
BUTTON_RADIUS = 8

# 操作带上的按钮一律等高。早先主/次/辅三档各差 4px，并排时上下边缘错开
# 2px，读到的是「没对齐」而不是「有层级」；同一行内的层级靠填充与描边
# 已经表达得足够清楚，尺寸差不需要再承担一次。
BUTTON_HEIGHT = 38
# 轻量动作（眉标行里的「清空」「复制报告」）退一档，不与正文抢高度
BUTTON_HEIGHT_SMALL = 30

# 底部读数坞里的两块文本窗（执行日志 / 模型体检）：同一档固定高度。
# 之前跟随 CTkTabview 的剩余空间伸缩，坞高又由页签内容决定，两侧互相试探；
# 现在窗高写死，页签高 = 页签栏 + 摘要行 + 固定窗，最外层滚动条负责浏览整块内容。
DOCK_BOX_HEIGHT = 260


# ---------- 灰阶三层 ----------
# 外壳是纯黑，面板与读数窗只靠亮度差浮在上面。
# 层差必须大到「肉眼不用比较就能分辨」：原 WELL/PANEL 只有 1.08:1，
# 在非全黑的笔记本屏上读数窗等于不存在，故整体拉开一档并补描边。
INK = "#000000"       # 仪器外壳 / 窗口底
PANEL = "#0D131B"     # 控制面板、卡片
WELL = "#080D14"      # 下沉读数窗：cURL、日志、体检明细（配 EDGE 描边）
EDGE = "#2A3849"      # 发丝分隔线（1.56:1，仍然「几乎看不见」）
EDGE_STRONG = "#3D5169"  # 输入框边框（2.29:1，必须能看见才谈得上可操作）

# ---------- 文字 ----------
TEXT_PRIMARY = "#EEF4FB"
TEXT_MUTED = "#9FB0C6"
# 4.25:1 不达 AA，眉标与辅助说明全是这个色，整体提亮一档到 5.50:1
TEXT_FAINT = "#7C8DA3"

# ---------- 信号色 ----------
# 主操作色：高饱和电蓝，压在近黑底上亮度差最大。
SIGNAL = "#4A92FF"      # 描边、进度条、字标、焦点环
SIGNAL_HOVER = "#3B7BF0"
# 主按钮填充单独一档：白字压 #2F7DFF 只有 3.82:1，不达 AA；
# 压深到 #2563EB 得 5.17:1，色相不变，仍然是全窗最亮的一块面。
SIGNAL_FILL = "#2563EB"
SIGNAL_DIM = "#1C3A6B"  # 低强度描边

# 焦点环：键盘焦点必须比「未聚焦」明显一个量级
FOCUS = "#4A92FF"

# 次级操作色：保留给图表、状态点缀，不再用于按钮填充
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
SURFACE_DIVIDER = EDGE_STRONG

CONTROL = SURFACE_INPUT
CONTROL_HOVER = "#1E2733"
DROPDOWN_BG = PANEL
DROPDOWN_HOVER = "#1E2733"
NEUTRAL_HOVER = "#25313F"

# 次级动作的实心底：比输入框亮一档，配 1px EDGE_STRONG 描边后
# 既明显退在主操作之后，又不会像纯文字按钮那样难以发现
NEUTRAL_FILL = "#1C2533"
NEUTRAL_EDGE = EDGE_STRONG

# 选中态底：分段控件与页签共用。
# 原分段选中色 #2C3E56 与未选中的输入框底色只差半个台阶，页签选中色
# #1C2533 又几乎等于面板色——两处「当前在哪」都要盯着比才看得出来。
# 这里统一提到一档，与未选中态拉开到肉眼不用比较就能分辨。
SELECTED_FILL = "#33475F"
SELECTED_HOVER = "#3D5470"
# 页签不是控件值，亮度比分段控件退一档，否则比正主「测试接口」还抢眼
TAB_SELECTED = "#243144"
TAB_SELECTED_HOVER = "#2E3E54"

CODE_TEXT = "#FFFFFF"

# 实心按钮上的文字：深蓝底用白字，中性底用正文色
ON_SIGNAL = "#FFFFFF"
ON_ACCENT = INK
