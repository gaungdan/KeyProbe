"""界面控件增强：统一 CustomTkinter 下拉框的箭头、弹层与宽度行为。

CustomTkinter 原生行为的三个问题：

1. ``CTkComboBox`` 只在右侧箭头区域绑定点击事件，点击输入框本体不会弹出下拉列表。
2. 下拉弹层宽度由 ``DropdownMenu.min_character_width``（默认 18 字符）决定，
   与控件自身宽度无关，导致长选项被压窄、和控件宽度不一致。
3. 箭头有两条绘制路径（三点折线 / 专用字形），粗细颜色都跟控件高度走，
   ComboBox 与 OptionMenu 命中哪条不保证一致；弹层是原生 ``tkinter.Menu``，
   条目文字贴边、分组标题还可点。

这里通过子类统一修正：弹层宽度按控件实际宽度与最长选项取大值，
并让 ``CTkComboBox`` 点击任意位置都能展开；箭头一律删掉自绘，
弹层条目补左内缩、分组标题置灰禁用。
"""

from __future__ import annotations

import tkinter
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from app.ui import theme

_ASSETS = Path(__file__).resolve().parent / "assets"


_ARROW_TAG = "keyprobe_dropdown_arrow"


def _paint_dropdown_arrow(widget, canvas, color: str) -> None:
    """删掉内置箭头，在右侧箭头区正中画一枚统一粗细的折线箭头。

    CustomTkinter 的箭头有两条绘制路径：三点折线（线宽取 ``控件高度 / 9``，
    36px 高的控件就是 4px 粗、12px 跨度的粗三角），或用专用字形字体渲染一个
    ``Y`` 字。两条路径的尺寸都跟着控件高度走、颜色都取 ``text_color``（近白），
    结果是一个又粗又亮的尖角，和界面里 1px 描线的眼睛、复制图标不是一套语言；
    两个控件各自命中哪条路径还不保证一致（旧版按坐标缩放的做法，遇上
    CTkCanvas 的 ``coords`` 取不到值就直接静默失效了）。

    这里统一删掉原生记号、按控件几何自绘同一组形状：ComboBox 与 OptionMenu
    并排时箭头的位置、大小、粗细完全相同。hover 只改颜色，不加底色块。
    """

    try:
        center_x = widget._apply_widget_scaling(
            widget._current_width - widget._current_height / 2
        )
        center_y = widget._apply_widget_scaling(widget._current_height / 2)
    except Exception:
        return

    try:
        canvas.delete("dropdown_arrow")
        canvas.delete(_ARROW_TAG)
        half_w = max(3.0, center_y * 0.30)
        half_h = half_w * 0.6
        canvas.create_line(
            center_x - half_w, center_y - half_h,
            center_x, center_y + half_h,
            center_x + half_w, center_y - half_h,
            width=2,
            joinstyle=tkinter.ROUND,
            capstyle=tkinter.ROUND,
            tags=(_ARROW_TAG,),
        )
        canvas.itemconfig(_ARROW_TAG, fill=color)
        canvas.tag_raise(_ARROW_TAG)
    except Exception:
        pass


def _set_arrow_color(canvas, color: str) -> None:
    try:
        canvas.itemconfig(_ARROW_TAG, fill=color)
    except Exception:
        pass


def _flatten_right_segment(canvas, fill: str, border: str | None = None) -> None:
    """把箭头那一侧的底色压成与字段本体一致，彻底去掉色块。

    CustomTkinter 把下拉框横向切成两段：左段用 ``fg_color``，右段（箭头区）
    用 ``button_color``。静止时两者同色看不出问题，但鼠标移进字段时
    CTk 会把 ``button_color`` 换成 ``button_hover_color``——于是在箭头周围
    浮出一块方形色块，看起来像贴了个补丁。

    这里每次重绘后把右段强制刷回 ``fg_color``，字段就始终是一整块面；
    hover 反馈改由箭头本身变亮承担。
    """

    try:
        for item in canvas.find_withtag("inner_parts_right"):
            for option in ("fill", "outline"):
                try:
                    canvas.itemconfig(item, **{option: fill})
                except Exception:
                    pass
        if border is not None:
            for item in canvas.find_withtag("border_parts_right"):
                for option in ("fill", "outline"):
                    try:
                        canvas.itemconfig(item, **{option: border})
                    except Exception:
                        pass
    except Exception:
        pass


def _bind_arrow_hover(widget, canvas) -> None:
    """hover 只让箭头变亮，不给字段加底色块。

    CTk 的 ``_on_enter`` 是直接 ``itemconfig("inner_parts_right", fill=hover)``，
    不经过 ``_draw``，所以在 ``_draw`` 里压平是拦不住的——必须在这里再压一次。
    本函数在 CTk 绑定之后才 bind（``add=True``），因此执行顺序在我们之后，
    正好覆盖掉它刚刷上的 hover 色。
    """

    def _fg() -> str:
        return widget._apply_appearance_mode(widget._fg_color)

    def _border() -> str | None:
        if hasattr(widget, "_border_color"):
            return widget._apply_appearance_mode(widget._border_color)
        return None

    def on_enter(_event=None) -> None:
        _flatten_right_segment(canvas, _fg(), _border())
        # 禁用态不点亮：CTk 自己也会跳过，这里保持一致
        if getattr(widget, "_state", None) == tkinter.DISABLED:
            _set_arrow_color(canvas, theme.TEXT_FAINT)
            return
        _set_arrow_color(canvas, theme.TEXT_PRIMARY)

    def on_leave(_event=None) -> None:
        _flatten_right_segment(canvas, _fg(), _border())
        disabled = getattr(widget, "_state", None) == tkinter.DISABLED
        _set_arrow_color(canvas, theme.TEXT_FAINT if disabled else theme.TEXT_MUTED)

    canvas.bind("<Enter>", on_enter, add=True)
    canvas.bind("<Leave>", on_leave, add=True)


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
# 不再使用默认主题里那块亮蓝按钮。边框与输入框一致，才看得出「这是个可填的框」。
_CONTROL_STYLE = {
    "fg_color": theme.SURFACE_INPUT,
    "button_color": theme.CONTROL,
    "button_hover_color": theme.CONTROL_HOVER,
    "border_color": theme.SURFACE_DIVIDER,
    "border_width": 1,
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


# 弹层文字统一左内缩：Windows 原生菜单的条目贴着边框排，整列读起来像表头。
# 原生菜单不暴露内边距选项（padtext / padname 在本机 Tk 构建里都报 unknown option），
# 只能在标签文本前补空格，且所有条目补得一样多，列才对齐。
_MENU_LEFT_PAD = "  "


def _style_menu_entries(widget, menu) -> None:
    """给弹层条目加左内缩，并把分组标题压成不可点的分隔行。

    分组标题（``── Claude Code（4）──``）以前也是可选项，选中后要等回调
    把它退回上一次的有效配置——视觉上它是选项，行为上它不是，两头对不上。
    这里直接置灰禁用，它只承担「这一组是什么」的说明职责。
    """

    try:
        headers = getattr(widget, "_header_values", None) or ()
        body_color = menu._apply_appearance_mode(menu._text_color)
        char_width = getattr(menu, "_min_character_width", 0) or 0
        for index, value in enumerate(menu._values or ()):
            text = str(value)
            label = _MENU_LEFT_PAD + (text.ljust(char_width) if char_width else text)
            is_header = text in headers
            menu.entryconfigure(
                index,
                label=label,
                state="disabled" if is_header else "normal",
                foreground=theme.TEXT_FAINT if is_header else body_color,
            )
    except Exception:
        pass


def _hook_menu_restyle(widget, menu) -> None:
    """让弹层每次重建条目后都重新套上统一样式。"""

    if getattr(menu, "_keyprobe_hooked", False):
        return
    original = menu._add_menu_commands

    def restyled() -> None:
        original()
        _style_menu_entries(widget, menu)

    menu._add_menu_commands = restyled
    menu._keyprobe_hooked = True


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
    """在尺寸变化和展开前同步弹层宽度，并统一弹层条目的呈现。"""

    def _bind_width_sync(self) -> None:
        self._last_sync_width = 0
        self._header_values: set[str] = set()
        self.bind("<Configure>", self._on_resize_sync, add=True)
        menu = getattr(self, "_dropdown_menu", None)
        if menu is not None:
            _hook_menu_restyle(self, menu)
            _style_menu_entries(self, menu)
        self.after_idle(lambda: sync_dropdown_width(self))

    def set_header_values(self, headers) -> None:
        """标记弹层里只作分组说明、不可选取的标题行。"""

        self._header_values = set(headers or ())
        menu = getattr(self, "_dropdown_menu", None)
        if menu is not None:
            _style_menu_entries(self, menu)

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

    ``placeholder_text`` 在输入框为空且未聚焦时显示灰字提示，
    此时 ``get()`` 返回空串，提示文字不会被当成真实取值。
    带占位提示的下拉框初始为空（不预选首项），语义上就是「尚未选定」。
    """

    def __init__(
        self,
        master,
        *,
        readonly: bool = False,
        placeholder_text: str = "",
        **kwargs,
    ) -> None:
        self._readonly = readonly
        self._placeholder_text = placeholder_text
        self._placeholder_shown = False
        if readonly:
            kwargs.setdefault("state", "readonly")
        for key, value in _CONTROL_STYLE.items():
            kwargs.setdefault(key, value)
        # 弹层字体跟随控件本体：模型 ID 是等宽字，列表里也该用等宽字读
        kwargs.setdefault("dropdown_font", kwargs.get("font"))
        super().__init__(master, **kwargs)
        # readonly 状态下 tkinter.Entry 拒绝 insert，父类初始化时写入的默认值会丢失，
        # 这里通过 set() 补回（set() 内部会临时解除 readonly）。
        if readonly and self._variable is None and self._values:
            self.set(self._values[0])
        # CTkComboBox.bind 会转发到内部 entry，这里需要直接绑定 entry
        self._entry.bind("<Button-1>", self._on_entry_click, add=True)
        self._entry.bind("<FocusIn>", self._clear_placeholder, add=True)
        self._entry.bind("<FocusOut>", self._restore_placeholder, add=True)
        # 有占位提示就意味着「尚未选定」，父类初始化时自动填入的首项（或演示文字
        # "CTkComboBox"）必须先清掉，否则占位提示永远不会出现。
        if placeholder_text and self._variable is None:
            self._replace_entry_text("")
        self._restore_placeholder()
        self._bind_width_sync()
        _bind_arrow_hover(self, self._canvas)

    def configure(self, require_redraw=False, **kwargs):
        # readonly 模式下调用方传入的 "normal" 需要还原成 "readonly"，
        # 否则解除锁定后输入框会重新变为可编辑。
        if self._readonly and kwargs.get("state") == "normal":
            kwargs["state"] = "readonly"
        super().configure(require_redraw=require_redraw, **kwargs)

    # ---------- 取值 ----------
    def get(self) -> str:
        return "" if self._placeholder_shown else super().get()

    def set(self, value: str) -> None:
        """写入取值；传空串则回到占位提示。"""

        self._placeholder_shown = False
        self._replace_entry_text(value)
        if value.strip():
            self._apply_entry_color()
        else:
            self._restore_placeholder()

    # ---------- 占位提示 ----------
    def _replace_entry_text(self, text: str) -> None:
        """写入输入框内容。readonly / disabled 的 Entry 会拒绝 insert，需临时解锁。"""

        locked = self._state in ("readonly", tkinter.DISABLED)
        if locked:
            self._entry.configure(state="normal")
        self._entry.delete(0, tkinter.END)
        if text:
            self._entry.insert(0, text)
        if locked:
            self._entry.configure(state=self._state)

    def _apply_entry_color(self) -> None:
        color = theme.TEXT_FAINT if self._placeholder_shown else self._text_color
        self._entry.configure(fg=self._apply_appearance_mode(color))

    def _clear_placeholder(self, event=None) -> None:
        if not self._placeholder_shown:
            return
        self._placeholder_shown = False
        self._replace_entry_text("")
        self._apply_entry_color()

    def _restore_placeholder(self, event=None) -> None:
        if not self._placeholder_text or self._placeholder_shown:
            return
        if self._entry.get().strip():
            return
        self._placeholder_shown = True
        self._replace_entry_text(self._placeholder_text)
        self._apply_entry_color()

    def _draw(self, no_color_updates: bool = False) -> None:
        # 父类 _draw 会按 text_color 重设 entry 前景色，重绘后需要把灰字改回来
        super()._draw(no_color_updates)
        if getattr(self, "_placeholder_shown", False):
            self._apply_entry_color()
        # 右段（箭头区）压回字段底色、描边压回边框色：
        # CTk 原本用 button_color 画这一段，静止时同色看不出问题，
        # 一 hover 就换成 button_hover_color，箭头周围浮出一块方形色块；
        # 同时那段描边也被画成底色，边框在箭头处整段断开。
        _flatten_right_segment(
            self._canvas,
            self._apply_appearance_mode(self._fg_color),
            self._apply_appearance_mode(self._border_color),
        )
        disabled = self._state == tkinter.DISABLED
        _paint_dropdown_arrow(
            self, self._canvas, theme.TEXT_FAINT if disabled else theme.TEXT_MUTED
        )

    def _dropdown_callback(self, value: str) -> None:
        self._placeholder_shown = False
        super()._dropdown_callback(value)
        self._apply_entry_color()

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
    """与 ComboBox 同款外观的下拉菜单：自带 1px 描边。

    ``CTkOptionMenu`` 的 ``_draw`` 把 border_width 硬编码成 0，天生没有边框，
    也不接受 ``border_color`` / ``border_width`` 参数（传了直接抛 ValueError）。
    早先的做法是在外面套一层 ``CTkFrame`` 补边框，结果是「外框 + 内层圆角」
    两圈线，箭头位置也比 ComboBox 内缩 1px——并排放着明显不是一套。

    这里改成直接重写 ``_draw``，把真实 border_width 交给绘制引擎：
    几何、描边、箭头位置与 ``ComboBox`` 完全一致，不再有第二层壳。
    """

    def __init__(self, master, **kwargs) -> None:
        # CTkOptionMenu 默认是亮蓝按钮，这里与 ComboBox 共用同一份配色表，
        # 两者的底色、边框、弹层色一律从同一个来源取值。
        for key, value in _CONTROL_STYLE.items():
            kwargs.setdefault(key, value)
        kwargs.setdefault("dropdown_font", kwargs.get("font"))
        border_color = kwargs.pop("border_color", theme.EDGE_STRONG)
        border_width = kwargs.pop("border_width", 1)
        # 必须在 super().__init__ 之前赋好：父类构造函数里就会调一次 _draw()
        self._border_color = border_color
        self._border_width = border_width
        super().__init__(master, **kwargs)
        self._bind_width_sync()
        _bind_arrow_hover(self, self._canvas)

    def _draw(self, no_color_updates: bool = False) -> None:
        """复刻 CTkOptionMenu._draw：换成真实 border_width，并自绘统一箭头。

        父类里那次 ``draw_dropdown_arrow`` 直接省掉——箭头由 ``_paint_dropdown_arrow``
        统一自绘，画出来再删掉只是白费一次重绘。
        """

        left_section_width = self._current_width - self._current_height
        requires_recoloring = self._draw_engine.draw_rounded_rect_with_border_vertical_split(
            self._apply_widget_scaling(self._current_width),
            self._apply_widget_scaling(self._current_height),
            self._apply_widget_scaling(self._corner_radius),
            self._apply_widget_scaling(self._border_width),
            self._apply_widget_scaling(left_section_width),
        )

        disabled = self._state == tkinter.DISABLED
        if no_color_updates is False or requires_recoloring:
            self._canvas.configure(bg=self._apply_appearance_mode(self._bg_color))
            self._canvas.itemconfig(
                "inner_parts_left",
                outline=self._apply_appearance_mode(self._fg_color),
                fill=self._apply_appearance_mode(self._fg_color),
            )
            self._canvas.itemconfig(
                "inner_parts_right",
                outline=self._apply_appearance_mode(self._button_color),
                fill=self._apply_appearance_mode(self._button_color),
            )
            self._canvas.itemconfig(
                "border_parts",
                outline=self._apply_appearance_mode(self._border_color),
                fill=self._apply_appearance_mode(self._border_color),
            )
            self._text_label.configure(bg=self._apply_appearance_mode(self._fg_color))
            self._text_label.configure(
                fg=self._apply_appearance_mode(
                    self._text_color_disabled if disabled else self._text_color
                )
            )

        # 箭头区压回字段底色（hover 时 CTk 会直接 itemconfig 上色，这里兜住）
        _flatten_right_segment(
            self._canvas,
            self._apply_appearance_mode(self._fg_color),
            self._apply_appearance_mode(self._border_color),
        )
        _paint_dropdown_arrow(
            self, self._canvas, theme.TEXT_FAINT if disabled else theme.TEXT_MUTED
        )

    def configure(self, require_redraw: bool = False, **kwargs):
        if "border_color" in kwargs:
            self._border_color = kwargs.pop("border_color")
            require_redraw = True
        if "border_width" in kwargs:
            self._border_width = kwargs.pop("border_width")
            require_redraw = True
        super().configure(require_redraw=require_redraw, **kwargs)
        return None

    config = configure


# ---------- 按钮 ----------
# 三级动作，靠「填充色 + 描边」区分，同一操作带内一律等高。
# 早先三级全实心、只靠蓝的深浅区分主次，但更亮的青蓝在黑底上反而更抢眼，
# 主次被读反了；三档各自的高度差又让并排的按钮上下边缘错开。
# 现在：主操作实心深蓝（全窗唯一）、次级中性实心加描边、辅助纯描边，
# 高度统一由调用方按所在行决定。


def _button_base(kwargs: dict, height: int) -> None:
    kwargs.setdefault("height", height)
    kwargs.setdefault("corner_radius", theme.BUTTON_RADIUS)
    kwargs.setdefault("border_width", 0)


def primary_button(master, **kwargs) -> ctk.CTkButton:
    """主操作：实心深蓝，全窗口只应出现一个。"""

    _button_base(kwargs, theme.BUTTON_HEIGHT)
    kwargs.setdefault("font", theme.font(14, "bold"))
    kwargs.setdefault("fg_color", theme.SIGNAL_FILL)
    kwargs.setdefault("hover_color", theme.SIGNAL_HOVER)
    kwargs.setdefault("text_color", theme.ON_SIGNAL)
    return ctk.CTkButton(master, **kwargs)


def secondary_button(master, **kwargs) -> ctk.CTkButton:
    """次级主操作：中性实心 + 1px 描边。

    比主操作暗一档，但仍有实体轮廓，不会被误认成主操作，
    也不会像纯文字按钮那样在深色底上找不到。
    """

    _button_base(kwargs, theme.BUTTON_HEIGHT)
    kwargs.setdefault("font", theme.font(13, "bold"))
    kwargs.setdefault("fg_color", theme.NEUTRAL_FILL)
    kwargs.setdefault("hover_color", theme.NEUTRAL_HOVER)
    kwargs.setdefault("text_color", theme.TEXT_PRIMARY)
    kwargs.setdefault("border_width", 1)
    kwargs.setdefault("border_color", theme.NEUTRAL_EDGE)
    return ctk.CTkButton(master, **kwargs)


def ghost_button(master, **kwargs) -> ctk.CTkButton:
    """辅助操作：透明底 + 1px 描边。

    早先是「纯文字按钮」——在纯黑底上完全没有轮廓，看上去不像可点的东西
    （「导入 CC Switch」就吃了这个亏）。现在补一圈 1px 描边：
    仍然明显退在实心按钮之后，但边界清楚，一眼知道能点。
    """

    _button_base(kwargs, theme.BUTTON_HEIGHT_SMALL)
    kwargs.setdefault("font", theme.font(12))
    kwargs.setdefault("fg_color", "transparent")
    kwargs.setdefault("hover_color", theme.NEUTRAL_HOVER)
    kwargs.setdefault("text_color", theme.TEXT_MUTED)
    # _button_base 已经把 border_width 设成 0，这里必须显式覆盖
    kwargs["border_width"] = 1
    kwargs.setdefault("border_color", theme.EDGE_STRONG)
    return ctk.CTkButton(master, **kwargs)


def hairline(master, **kwargs) -> ctk.CTkFrame:
    """1px 分隔线：只用于把同一块面板里的字段分组，不额外占行高。"""

    kwargs.setdefault("height", 1)
    kwargs.setdefault("corner_radius", 0)
    kwargs.setdefault("fg_color", theme.EDGE)
    return ctk.CTkFrame(master, **kwargs)


# ---------- 焦点环 ----------
def bind_focus_ring(widget, target=None, *, ring: str = theme.FOCUS) -> None:
    """给输入类控件挂焦点环。

    CustomTkinter 的 Entry / ComboBox 边框色是静态的，聚焦与不聚焦完全一样，
    键盘用户 Tab 一遍表单完全不知道自己在哪。这里统一把「聚焦」映射为
    高对比描边 + 加粗，失焦后复原。

    ``_error_ring`` 置真时焦点环让位给错误描边：``_show_form_error`` 先染红
    再 ``focus_set``，若不拦这一步，FocusIn 会立刻把红框刷成蓝环，
    「哪个字段出错」这个线索就只剩文字了。
    """

    box = target if target is not None else widget
    resting_color = box.cget("border_color")
    resting_width = box.cget("border_width") or 1

    def on_focus(_event=None) -> None:
        if getattr(box, "_error_ring", False):
            return
        try:
            box.configure(border_color=ring, border_width=2)
        except Exception:
            pass

    def on_blur(_event=None) -> None:
        if getattr(box, "_error_ring", False):
            try:
                box.configure(border_color=theme.ERROR, border_width=2)
            except Exception:
                pass
            return
        try:
            box.configure(border_color=resting_color, border_width=resting_width)
        except Exception:
            pass

    widget.bind("<FocusIn>", on_focus, add=True)
    widget.bind("<FocusOut>", on_blur, add=True)
    return widget
