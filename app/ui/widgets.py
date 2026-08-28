"""界面控件增强：修正 CustomTkinter 下拉框的宽度与点击行为。

CustomTkinter 原生行为的两个问题：

1. ``CTkComboBox`` 只在右侧箭头区域绑定点击事件，点击输入框本体不会弹出下拉列表。
2. 下拉弹层宽度由 ``DropdownMenu.min_character_width``（默认 18 字符）决定，
   与控件自身宽度无关，导致长选项被压窄、和控件宽度不一致。

这里通过子类统一修正：弹层宽度按控件实际宽度与最长选项取大值，
并让 ``CTkComboBox`` 点击任意位置都能展开。
"""

from __future__ import annotations

import tkinter
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.ui import theme

_ASSETS = Path(__file__).resolve().parent / "assets"


def load_icon(name: str, size: int = 20) -> ctk.CTkImage:
    """按名字加载图标。``<name>.png`` 是深色模式用的白色描线，
    ``<name>-light.png`` 是浅色模式用的黑色描线。"""

    light = Image.open(_ASSETS / f"{name}-light.png").resize((size, size), Image.LANCZOS)
    dark = Image.open(_ASSETS / f"{name}.png").resize((size, size), Image.LANCZOS)
    return ctk.CTkImage(light_image=light, dark_image=dark, size=(size, size))


def icon_button(master, icon: ctk.CTkImage, **kwargs) -> ctk.CTkButton:
    """方形图标按钮：只承载一个动作，不占文字宽度。"""

    side = kwargs.pop("side", 26)
    kwargs.setdefault("width", side)
    kwargs.setdefault("height", side)
    kwargs.setdefault("corner_radius", 6)
    kwargs.setdefault("border_width", 0)
    kwargs.setdefault("fg_color", theme.NEUTRAL_FILL)
    kwargs.setdefault("hover_color", theme.NEUTRAL_HOVER)
    return ctk.CTkButton(master, image=icon, text="", **kwargs)


# 弹层字符宽度上下限，避免极端值把菜单撑爆或压扁
_MIN_CHARS = 18
_MAX_CHARS = 160

# 下拉控件统一配色：箭头按钮与输入框本体同色，弹层跟随窗口灰阶，
# 不再使用默认主题里那块亮蓝按钮。
_CONTROL_STYLE = {
    "fg_color": theme.SURFACE_INPUT,
    "button_color": theme.CONTROL,
    "button_hover_color": theme.CONTROL_HOVER,
    "border_color": theme.SURFACE_DIVIDER,
    "dropdown_fg_color": theme.DROPDOWN_BG,
    "dropdown_hover_color": theme.DROPDOWN_HOVER,
    "dropdown_text_color": theme.TEXT_PRIMARY,
    "text_color": theme.TEXT_PRIMARY,
}


def _menu_char_width(menu) -> int:
    """估算下拉弹层字体的单字符像素宽度。"""

    try:
        font_spec = menu._apply_font_scaling(menu._font)
        return max(5, tkfont.Font(font=font_spec).measure("0"))
    except Exception:
        return 8


def sync_dropdown_width(widget) -> None:
    """让下拉弹层宽度不小于控件本身宽度，同时容纳最长选项。"""

    menu = getattr(widget, "_dropdown_menu", None)
    if menu is None:
        return

    try:
        widget.update_idletasks()
        pixel_width = widget.winfo_width()
        if pixel_width <= 1:
            pixel_width = widget.cget("width")

        char_width = _menu_char_width(menu)
        by_widget = int(pixel_width / char_width) - 2
        by_values = max((len(str(v)) for v in menu.cget("values") or []), default=0)

        chars = max(_MIN_CHARS, by_widget, by_values)
        chars = min(_MAX_CHARS, chars)

        if menu.cget("min_character_width") != chars:
            menu._min_character_width = chars
            menu._add_menu_commands()
    except Exception:
        pass


class _WidthSyncMixin:
    """在尺寸变化和展开前同步弹层宽度。"""

    def _bind_width_sync(self) -> None:
        self._last_sync_width = 0
        self.bind("<Configure>", self._on_resize_sync, add=True)
        self.after_idle(lambda: sync_dropdown_width(self))

    def _on_resize_sync(self, event=None) -> None:
        width = getattr(event, "width", 0) or 0
        if abs(width - getattr(self, "_last_sync_width", 0)) < 8:
            return
        self._last_sync_width = width
        sync_dropdown_width(self)

    def _open_dropdown_menu(self):  # noqa: D102 - 复用父类语义
        sync_dropdown_width(self)
        super()._open_dropdown_menu()


class ComboBox(_WidthSyncMixin, ctk.CTkComboBox):
    """点击任意位置即可展开、弹层宽度跟随控件的下拉框。

    默认保留手动输入能力：弹层关闭后输入框已获得焦点，再次点击不会重复弹出。
    传入 ``readonly=True`` 则退化为纯选择器，禁止键盘输入，
    点击任意位置总是展开下拉列表。
    """

    def __init__(self, master, *, readonly: bool = False, **kwargs) -> None:
        self._readonly = readonly
        if readonly:
            kwargs.setdefault("state", "readonly")
        for key, value in _CONTROL_STYLE.items():
            kwargs.setdefault(key, value)
        super().__init__(master, **kwargs)
        # readonly 状态下 tkinter.Entry 拒绝 insert，父类初始化时写入的默认值会丢失，
        # 这里通过 set() 补回（set() 内部会临时解除 readonly）。
        if readonly and self._variable is None and self._values:
            self.set(self._values[0])
        # CTkComboBox.bind 会转发到内部 entry，这里需要直接绑定 entry
        self._entry.bind("<Button-1>", self._on_entry_click, add=True)
        self._bind_width_sync()

    def configure(self, require_redraw=False, **kwargs):
        # readonly 模式下调用方传入的 "normal" 需要还原成 "readonly"，
        # 否则解除锁定后输入框会重新变为可编辑。
        if self._readonly and kwargs.get("state") == "normal":
            kwargs["state"] = "readonly"
        super().configure(require_redraw=require_redraw, **kwargs)

    def _on_entry_click(self, event=None):
        if self._state == tkinter.DISABLED or not self._values:
            return None
        if not self._readonly:
            # 输入框已聚焦时视为输入意图，不再弹出下拉，避免干扰手动输入
            try:
                if self.focus_get() is self._entry:
                    return None
            except Exception:
                pass
        self._entry.focus_set()
        self._open_dropdown_menu()
        return "break"


class OptionMenu(_WidthSyncMixin, ctk.CTkOptionMenu):
    """弹层宽度跟随控件宽度的下拉菜单。"""

    def __init__(self, master, **kwargs) -> None:
        # CTkOptionMenu 默认是亮蓝按钮，这里改为与 ComboBox 一致的灰阶
        kwargs.setdefault("fg_color", theme.SURFACE_INPUT)
        kwargs.setdefault("button_color", theme.CONTROL)
        kwargs.setdefault("button_hover_color", theme.CONTROL_HOVER)
        kwargs.setdefault("dropdown_fg_color", theme.DROPDOWN_BG)
        kwargs.setdefault("dropdown_hover_color", theme.DROPDOWN_HOVER)
        kwargs.setdefault("dropdown_text_color", theme.TEXT_PRIMARY)
        kwargs.setdefault("text_color", theme.TEXT_PRIMARY)
        super().__init__(master, **kwargs)
        self._bind_width_sync()


# ---------- 按钮 ----------
# 三个层级全部实心：近黑底上描边按钮的对比度不够，用户会先扫一遍才找到操作。
# 主次关系靠色相与字重区分，尺寸统一，形成一条整齐的操作条。


def _button_base(kwargs: dict) -> None:
    kwargs.setdefault("height", theme.BUTTON_HEIGHT)
    kwargs.setdefault("corner_radius", theme.BUTTON_RADIUS)
    kwargs.setdefault("border_width", 0)


def primary_button(master, **kwargs) -> ctk.CTkButton:
    """主操作：实心电紫，全窗口只应出现一个。"""

    _button_base(kwargs)
    kwargs.setdefault("font", theme.font(14, "bold"))
    kwargs.setdefault("fg_color", theme.SIGNAL)
    kwargs.setdefault("hover_color", theme.SIGNAL_HOVER)
    kwargs.setdefault("text_color", theme.ON_SIGNAL)
    return ctk.CTkButton(master, **kwargs)


def signal_button(master, **kwargs) -> ctk.CTkButton:
    """次要主操作：实心青，与主操作同等醒目但色相不同，不会被误点。"""

    _button_base(kwargs)
    kwargs.setdefault("font", theme.font(14, "bold"))
    kwargs.setdefault("fg_color", theme.ACCENT)
    kwargs.setdefault("hover_color", theme.ACCENT_HOVER)
    kwargs.setdefault("text_color", theme.ON_ACCENT)
    return ctk.CTkButton(master, **kwargs)


def ghost_button(master, **kwargs) -> ctk.CTkButton:
    """辅助操作：实心中性色，可见但明确退在两个彩色按钮之后。"""

    _button_base(kwargs)
    kwargs.setdefault("font", theme.font(13))
    kwargs.setdefault("fg_color", theme.NEUTRAL_FILL)
    kwargs.setdefault("hover_color", theme.NEUTRAL_HOVER)
    kwargs.setdefault("text_color", theme.TEXT_PRIMARY)
    return ctk.CTkButton(master, **kwargs)
