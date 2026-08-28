"""日志面板：实时日志输出 + 清空。"""

from typing import Callable, Optional

import customtkinter as ctk

from app.ui import theme
from app.ui.widgets import ghost_button
from app.utils.textbox import fit_to_content, hide_scrollbar


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
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.TEXT_PRIMARY,
            border_width=0,
        )
        # 不 expand：高度随日志条数撑开，交给外层滚动条统一滚动。
        self.log_text.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_XS))
        hide_scrollbar(self.log_text)
        fit_to_content(self.log_text)

    def log(self, message: str) -> None:
        """追加一行日志并滚动到底部。"""

        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        fit_to_content(self.log_text)

    def clear(self) -> None:
        """清空文本框。"""

        self.log_text.delete("1.0", "end")
        fit_to_content(self.log_text)

    def _clear(self) -> None:
        self.clear()
        if self._on_clear:
            self._on_clear()
