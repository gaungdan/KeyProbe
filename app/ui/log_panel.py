"""日志面板：实时日志输出 + 清空。"""

from typing import Callable, Optional

import customtkinter as ctk

from app.ui import theme
from app.ui.widgets import ghost_button
from app.utils.textbox import hide_scrollbar


class LogPanel(ctk.CTkFrame):
    """日志输出面板。"""

    def __init__(
        self,
        master,
        on_clear: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_clear = on_clear
        self._create_widgets()

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
            width=60,
            height=26,
            font=theme.font(12),
            command=self._clear,
        ).pack(side="right")

        self.log_text = ctk.CTkTextbox(
            self,
            height=200,
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.TEXT_PRIMARY,
            border_width=0,
        )
        # 固定高度，由外层 CTkScrollableFrame 统一滚动，不随日志增长撑高布局。
        self.log_text.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_XS))
        hide_scrollbar(self.log_text)
        # 拦截滚轮事件，避免滚动日志时联动外层 CTkScrollableFrame 一起滚
        self._bind_scroll_block(self.log_text)

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

        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def clear(self) -> None:
        """清空文本框。"""

        self.log_text.delete("1.0", "end")

    def _clear(self) -> None:
        self.clear()
        if self._on_clear:
            self._on_clear()
