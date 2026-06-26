"""结果展示面板：状态、延迟、Provider、模型数指标 + cURL 预览。"""

import customtkinter as ctk

from app.models.check import CheckStatus

STATUS_COLORS = {
    CheckStatus.VALID: "#22c55e",
    CheckStatus.INVALID_KEY: "#ef4444",
    CheckStatus.SERVER_ERROR: "#ef4444",
    CheckStatus.NETWORK_ERROR: "#ef4444",
    CheckStatus.RATE_LIMITED: "#f59e0b",
    CheckStatus.INSUFFICIENT_BALANCE: "#f59e0b",
    CheckStatus.UNKNOWN: "#9ca3af",
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
    """展示检测结果指标与 cURL 预览。"""

    def __init__(self, master) -> None:
        super().__init__(master)
        self._create_widgets()

    def _create_widgets(self) -> None:
        metrics = ctk.CTkFrame(self, fg_color="transparent")
        metrics.pack(fill="x", padx=15, pady=(12, 6))
        for i in range(4):
            metrics.grid_columnconfigure(i, weight=1)

        self.status_value = self._metric(metrics, 0, "状态", "—")
        self.latency_value = self._metric(metrics, 1, "延迟", "—")
        self.provider_value = self._metric(metrics, 2, "服务商/协议", "—")
        self.model_count_value = self._metric(metrics, 3, "模型数", "0")

        curl_label = ctk.CTkLabel(
            self, text="请求预览", font=("Helvetica", 13, "bold")
        )
        curl_label.pack(anchor="w", padx=15, pady=(6, 0))

        self.curl_preview = ctk.CTkTextbox(
            self,
            height=90,
            font=("Consolas", 12),
            fg_color="#1E1E1E",
            text_color="#9ca3af",
        )
        self.curl_preview.pack(fill="x", padx=15, pady=(2, 12))

    def _metric(self, master, col: int, label: str, value: str) -> ctk.CTkLabel:
        card = ctk.CTkFrame(master)
        card.grid(row=0, column=col, padx=6, sticky="ew")
        ctk.CTkLabel(card, text=label, font=("Helvetica", 12), text_color="#9ca3af").pack(
            pady=(10, 2)
        )
        value_label = ctk.CTkLabel(card, text=value, font=("Helvetica", 18, "bold"))
        value_label.pack(pady=(0, 10))
        return value_label

    def set_status(self, status: CheckStatus) -> None:
        """更新状态文本与颜色。"""

        self.status_value.configure(
            text=STATUS_LABELS.get(status, "未知"),
            text_color=STATUS_COLORS.get(status, "#9ca3af"),
        )

    def set_latency(self, latency_ms: int | None) -> None:
        """更新延迟。"""

        self.latency_value.configure(
            text=f"{latency_ms} ms" if latency_ms is not None else "—"
        )

    def set_provider(self, provider: str) -> None:
        """更新服务商/协议。"""

        self.provider_value.configure(text=provider or "—")

    def set_model_count(self, count: int) -> None:
        """更新模型数。"""

        self.model_count_value.configure(text=str(count))

    def set_curl(self, curl: str) -> None:
        """更新 cURL 预览。"""

        self.curl_preview.delete("1.0", "end")
        self.curl_preview.insert("1.0", curl)
