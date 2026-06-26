"""日志面板组件：实时日志输出 + 清空。"""

from typing import Callable, Optional

import customtkinter as ctk


class LogPanel(ctk.CTkFrame):
    """日志输出面板。"""

    def __init__(
        self,
        master,
        on_clear: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(master)
        self._on_clear = on_clear
        self._create_widgets()

    def _create_widgets(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=15, pady=(10, 0))

        title = ctk.CTkLabel(
            header,
            text="执行日志",
            font=("Helvetica", 15, "bold"),
        )
        title.pack(side="left")

        ctk.CTkButton(
            header,
            text="清空",
            width=64,
            height=28,
            fg_color="#666666",
            hover_color="#555555",
            command=self._clear,
        ).pack(side="right", padx=4)

        self.log_text = ctk.CTkTextbox(
            self,
            font=("Consolas", 13),
            fg_color="#1E1E1E",
            text_color="#E0E0E0",
        )
        self.log_text.pack(fill="both", expand=True, padx=15, pady=10)

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
