"""日志面板：实时日志输出 + 清空。"""

import re
from typing import Callable, Optional

import customtkinter as ctk

from app.ui import theme
from app.ui.widgets import ghost_button
from app.utils.textbox import tint_scrollbar

# 日志行形如 "[14:22:07] [ERROR] 消息"，级别夹在第二对方括号里
_LEVEL_PATTERN = re.compile(r"^\[[^\]]*\]\s*\[([A-Z]+)\]")

_LEVEL_TAGS = {
    "INFO": theme.TEXT_PRIMARY,
    "SUCCESS": theme.OK,
    "WARNING": theme.WARN,
    "ERROR": theme.ERROR,
}

_EMPTY_HINT = "等待操作 · 每次请求的请求体、状态码与耗时都会记录在这里"


class LogPanel(ctk.CTkFrame):
    """日志输出面板。"""

    def __init__(
        self,
        master,
        on_clear: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_clear = on_clear
        self._empty_hint_shown = False
        self._line_count = 0
        self._create_widgets()
        self._show_empty_hint()

    def _create_widgets(self) -> None:
        # 页签已经写着「执行日志」，面板内不再重复标题，只留操作
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.GAP_XS, pady=(theme.GAP_XS, theme.GAP_SM))
        ctk.CTkLabel(
            header,
            text=theme.caps("output"),
            font=theme.mono(9, "bold"),
            text_color=theme.TEXT_FAINT,
        ).pack(side="left")
        ghost_button(
            header,
            text="清空",
            width=56,
            command=self._clear,
        ).pack(side="right")

        # 与「模型体检」页的身份核对行等高：两页请求的高度一致，切页签时
        # 整块读数坞不跳高，外层滚动条的位置也不会跟着漂。
        self.counts_label = ctk.CTkLabel(
            self, text="", font=theme.mono(11), text_color=theme.TEXT_MUTED, anchor="w"
        )
        self.counts_label.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_SM))

        self.log_text = ctk.CTkTextbox(
            self,
            height=theme.DOCK_BOX_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.TEXT_PRIMARY,
            border_width=1,
            border_color=theme.EDGE,
            corner_radius=8,
        )
        # 固定高度、与「模型体检」页同档：窗高不再随页签剩余空间伸缩，
        # 切页签时整块坞的高度不会跳，日志自己在一屏之内滚动。
        self.log_text.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_XS))
        # 日志窗自己滚动，滚动条不能藏：藏掉之后溢出就没有任何线索。
        # 压成与窗同底的细轨，看得见但不抢正文。
        tint_scrollbar(
            self.log_text,
            track=theme.WELL,
            thumb=theme.EDGE,
            thumb_hover=theme.SIGNAL_DIM,
        )
        self._configure_tags()
        # 拦截滚轮事件，避免滚动日志时联动外层 CTkScrollableFrame 一起滚
        self._bind_scroll_block(self.log_text)

    def _configure_tags(self) -> None:
        """按级别上色：级别信息本来就在文本里，只是之前没有变成可读的视觉线索。"""

        for level, color in _LEVEL_TAGS.items():
            self.log_text.tag_config(f"level-{level}", foreground=color)
        self.log_text.tag_config("empty-hint", foreground=theme.TEXT_FAINT)

    def _show_empty_hint(self) -> None:
        """空态给一句说明，而不是留一块纯黑。"""

        if self._empty_hint_shown:
            return
        self.log_text.insert("end", _EMPTY_HINT + "\n", "empty-hint")
        self._empty_hint_shown = True

    @staticmethod
    def _bind_scroll_block(widget: ctk.CTkTextbox) -> None:
        """绑定滚轮事件：手动滚动文本框并阻止向外传播，避免内外联动。"""

        def on_scroll(event) -> str:
            inner = getattr(widget, "_textbox", widget)
            # Windows: event.delta > 0 向上滚, < 0 向下滚, 步长 120
            if hasattr(event, "delta") and event.delta:
                units = -1 if event.delta > 0 else 1
                inner.yview_scroll(units, "units")
            return "break"

        def on_scroll_up(event) -> str:
            inner = getattr(widget, "_textbox", widget)
            inner.yview_scroll(-3, "units")
            return "break"

        def on_scroll_down(event) -> str:
            inner = getattr(widget, "_textbox", widget)
            inner.yview_scroll(3, "units")
            return "break"

        widget.bind("<MouseWheel>", on_scroll)
        widget.bind("<Button-4>", on_scroll_up)
        widget.bind("<Button-5>", on_scroll_down)

    def log(self, message: str) -> None:
        """追加一行日志并滚动到底部。"""

        if self._empty_hint_shown:
            self.log_text.delete("1.0", "end")
            self._empty_hint_shown = False

        match = _LEVEL_PATTERN.match(message)
        tag = f"level-{match.group(1)}" if match else None
        if tag:
            self.log_text.insert("end", message + "\n", tag)
        else:
            self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self._line_count += 1
        self._render_counts()

    def clear(self) -> None:
        """清空文本框。"""

        self.log_text.delete("1.0", "end")
        self._empty_hint_shown = False
        self._line_count = 0
        self._render_counts()
        self._show_empty_hint()

    def _render_counts(self) -> None:
        self.counts_label.configure(
            text=f"共 {self._line_count} 条" if self._line_count else ""
        )

    def _clear(self) -> None:
        self.clear()
        if self._on_clear:
            self._on_clear()
