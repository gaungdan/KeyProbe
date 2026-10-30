"""结果面板：状态读数条 + cURL 预览。

四张等宽指标卡把「状态」和「模型数」摆在同一视觉层级，但用户打开这个工具
只为回答一个问题——这把密钥能不能用。所以这里改成仪器式读数条：
左侧一道随状态变色的竖条 + 大号等宽状态字，延迟作为右侧读数，
服务商与模型数降为次级说明行。整块比原来矮 90px 左右，焦点只有一个。
"""

from typing import Callable, Optional

import customtkinter as ctk

from app.models.check import CheckStatus
from app.ui import theme
from app.ui.widgets import icon_button, load_icon
from app.utils.textbox import fit_to_wrapped_content, hide_scrollbar

STATUS_COLORS = {
    CheckStatus.VALID: theme.OK,
    CheckStatus.INVALID_KEY: theme.ERROR,
    CheckStatus.SERVER_ERROR: theme.ERROR,
    CheckStatus.NETWORK_ERROR: theme.ERROR,
    CheckStatus.RATE_LIMITED: theme.WARN,
    CheckStatus.INSUFFICIENT_BALANCE: theme.WARN,
    CheckStatus.UNKNOWN: theme.IDLE,
}

STATUS_LABELS = {
    CheckStatus.VALID: "有效",
    CheckStatus.INVALID_KEY: "密钥无效",
    CheckStatus.SERVER_ERROR: "服务错误",
    CheckStatus.NETWORK_ERROR: "网络错误",
    CheckStatus.RATE_LIMITED: "请求受限",
    CheckStatus.INSUFFICIENT_BALANCE: "余额不足",
    CheckStatus.UNKNOWN: "未知",
}


class ResultPanel(ctk.CTkFrame):
    """状态读数条与 cURL 预览。"""

    def __init__(self, master, on_copy: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master, fg_color=theme.PANEL)
        self._provider = ""
        self._model_count = 0
        self._on_copy = on_copy
        self._curl_text = ""
        self._create_widgets()

    def _create_widgets(self) -> None:
        readout = ctk.CTkFrame(self, fg_color="transparent")
        readout.pack(fill="x", padx=theme.GAP_LG, pady=(theme.GAP_MD, theme.GAP_XS))
        readout.grid_columnconfigure(1, weight=1)
        self._build_readout(readout)
        self._build_curl()

    def _build_readout(self, parent) -> None:
        # 随状态变色的竖条：整块面板里唯一一处「一眼可辨」的元素。
        # height 必须显式给小值——CTkFrame 默认 200px，会把整行撑高。
        self.state_bar = ctk.CTkFrame(
            parent, width=4, height=1, corner_radius=2, fg_color=theme.IDLE
        )
        self.state_bar.grid(row=0, column=0, sticky="ns", rowspan=2)

        self.status_value = ctk.CTkLabel(
            parent,
            text="待检测",
            font=theme.mono(25, "bold"),
            text_color=theme.IDLE,
            anchor="w",
        )
        self.status_value.grid(row=0, column=1, sticky="w", padx=(theme.GAP_MD, 0))

        self.context_value = ctk.CTkLabel(
            parent,
            text="填入接口地址与密钥后开始",
            font=theme.mono(11),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        )
        self.context_value.grid(row=1, column=1, sticky="w", padx=(theme.GAP_MD, 0))

        # 延迟读数：眉标 + 数值 + 单位三段式，与左栏「状态 + 说明」形成对称结构。
        # 早先只有一个孤零零的「— ms」，空态时读起来是「破折号毫秒」，
        # 用户根本不知道这个数字是什么。
        latency_box = ctk.CTkFrame(parent, fg_color="transparent")
        latency_box.grid(row=0, column=2, rowspan=2, sticky="e")
        ctk.CTkLabel(
            latency_box,
            text=theme.caps("latency"),
            font=theme.mono(9, "bold"),
            text_color=theme.TEXT_FAINT,
            anchor="e",
        ).pack(anchor="e")

        latency_row = ctk.CTkFrame(latency_box, fg_color="transparent")
        latency_row.pack(anchor="e")
        self.latency_value = ctk.CTkLabel(
            latency_row, text="—", font=theme.mono(23, "bold"), text_color=theme.TEXT_PRIMARY
        )
        self.latency_value.pack(side="left", anchor="s")
        self.latency_unit = ctk.CTkLabel(
            latency_row, text="ms", font=theme.mono(10), text_color=theme.TEXT_FAINT
        )
        self.latency_unit.pack(side="left", anchor="s", padx=(theme.GAP_XS, 0), pady=(0, 4))
        self.latency_unit.pack_forget()

    def _build_curl(self) -> None:
        # 眉标与复制按钮同行：按钮贴在读数窗右上角，和它作用的对象靠在一起
        curl_header = ctk.CTkFrame(self, fg_color="transparent")
        curl_header.pack(fill="x", padx=theme.GAP_LG, pady=(0, theme.GAP_XS))
        ctk.CTkLabel(
            curl_header,
            text=theme.caps("request"),
            font=theme.mono(9, "bold"),
            text_color=theme.TEXT_FAINT,
            anchor="w",
        ).pack(side="left")

        self._copy_icon = load_icon("copy", 14)
        self.copy_btn = icon_button(
            curl_header,
            self._copy_icon,
            side=24,
            state="disabled",
            command=self._copy_curl,
        )
        self.copy_btn.pack(side="right")

        self.curl_preview = ctk.CTkTextbox(
            self,
            height=76,
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.CODE_TEXT,
            border_width=1,
            border_color=theme.EDGE,
            corner_radius=8,
            wrap="char",
            activate_scrollbars=False,
        )
        self.curl_preview.pack(fill="x", padx=theme.GAP_LG, pady=(0, theme.GAP_SM))
        hide_scrollbar(self.curl_preview)
        # 宽度变化时重新计算换行后的高度，保证内容始终完整展示
        self.curl_preview.bind("<Configure>", self._on_curl_resize)
        self._curl_last_width = 0

    # ---------- 读数更新 ----------
    def set_status(self, status: CheckStatus) -> None:
        """更新状态文本、文字色与左侧竖条。"""

        color = STATUS_COLORS.get(status, theme.IDLE)
        self.status_value.configure(text=STATUS_LABELS.get(status, "未知"), text_color=color)
        self.state_bar.configure(fg_color=color)

    def set_latency(self, latency_ms: int | None) -> None:
        """更新延迟读数。空值只显示破折号——单位在没有数值时没有意义。"""

        if latency_ms is None:
            self.latency_value.configure(text="—")
            self.latency_unit.pack_forget()
            return
        self.latency_value.configure(text=str(latency_ms))
        self.latency_unit.pack(side="left", anchor="s", padx=(theme.GAP_XS, 0), pady=(0, 4))

    def set_provider(self, provider: str) -> None:
        """更新服务商/协议。"""

        self._provider = provider or ""
        self._render_context()

    def set_model_count(self, count: int) -> None:
        """更新模型数。"""

        self._model_count = count
        self._render_context()

    def _render_context(self) -> None:
        """服务商与模型数合成一行次级读数，长协议名不再挤压指标卡。"""

        parts = []
        if self._provider:
            parts.append(self._provider)
        if self._model_count:
            parts.append(f"{self._model_count} 个模型")
        self.context_value.configure(text=" · ".join(parts) or "填入接口地址与密钥后开始")

    def set_curl(self, curl: str) -> None:
        """更新 cURL 预览。

        高度只在「提交后」重算一次，且上下限收窄到 88–140px：
        之前每次按键都重算、上限到 400px，输入 URL 的过程中整页内容会跟着上下跳。
        """

        self._curl_text = curl
        self.copy_btn.configure(state="normal" if curl else "disabled")
        self.curl_preview.delete("1.0", "end")
        self.curl_preview.insert("1.0", curl)
        self.after_idle(
            lambda: fit_to_wrapped_content(
                self.curl_preview, line_height=20, min_height=76, max_height=150
            )
        )

    def _copy_curl(self) -> None:
        if self._curl_text and self._on_copy:
            self._on_copy(self._curl_text)

    def _on_curl_resize(self, event=None) -> None:
        """宽度变化会改变换行结果，需要重新计算高度。"""

        width = event.width if event is not None else 0
        if abs(width - self._curl_last_width) < 8:
            return
        self._curl_last_width = width
        self.after_idle(
            lambda: fit_to_wrapped_content(
                self.curl_preview, line_height=20, min_height=76, max_height=150
            )
        )
