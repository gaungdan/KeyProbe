"""模型体检面板：总评 + 三个维度的逐项结论 + 完整报告文本。"""

from __future__ import annotations

from typing import Callable, Optional

import customtkinter as ctk

from app.models.audit import (
    CATEGORY_CAPABILITY,
    CATEGORY_IDENTITY,
    CATEGORY_PARAMS,
    CONFIDENCE_LABELS,
    VERDICT_COLORS,
    VERDICT_LABELS,
    AuditReport,
    Verdict,
)
from app.ui import theme
from app.ui.widgets import ghost_button
from app.utils.textbox import tint_scrollbar

_SUMMARY_HINTS = {
    Verdict.PASS: "未发现异常",
    Verdict.WARN: "存在可疑项，建议查看详情",
    Verdict.FAIL: "发现异常项",
    Verdict.RATE_LIMITED: "部分探针因限流未测成，建议稍后重试",
    Verdict.SKIP: "全部项目已跳过",
    Verdict.UNKNOWN: "证据不足",
}


_IDENTITY_PLACEHOLDER = "配置模型 —　│　实际回传 —　│　模型自述 —"


class AuditPanel(ctk.CTkFrame):
    """展示模型体检报告。"""

    def __init__(self, master, on_copy: Optional[Callable[[str], None]] = None) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_copy = on_copy
        self._report_text = ""
        self._create_widgets()

    def _create_widgets(self) -> None:
        # 页签已经写着「模型体检」，面板内不再重复标题
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.GAP_XS, pady=(theme.GAP_XS, theme.GAP_SM))

        self.verdict_label = ctk.CTkLabel(
            header,
            text="尚未体检",
            font=theme.mono(15, "bold"),
            text_color=theme.IDLE,
            anchor="w",
        )
        self.verdict_label.pack(side="left")

        self.hint_label = ctk.CTkLabel(
            header,
            text="选好模型后点「模型体检」",
            font=theme.font(12),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        )
        self.hint_label.pack(side="left", padx=(theme.GAP_MD, 0))

        self.copy_btn = ghost_button(
            header,
            text="复制报告",
            width=80,
            font=theme.font(12),
            state="disabled",
            command=self._copy_report,
        )
        self.copy_btn.pack(side="right")

        # 计数与总评同行：它俩读的是同一件事（这份报告有多严重、跑了多少）。
        # 早先各占一行，把摘要顶到 88px 高，而页签总高是固定的——
        # 明细窗只剩 74px，报告正文连三行都放不下。
        self.counts_label = ctk.CTkLabel(
            header, text="", font=theme.mono(11), text_color=theme.TEXT_MUTED,
            anchor="e",
        )
        self.counts_label.pack(side="right", padx=(0, theme.GAP_MD))

        # 身份核对单独一行：三个模型名要逐字比对，塞进总评行会被裁切
        self.identity_label = ctk.CTkLabel(
            self,
            text=_IDENTITY_PLACEHOLDER,
            font=theme.mono(11),
            text_color=theme.TEXT_FAINT,
            justify="left",
            anchor="w",
        )
        self.identity_label.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_SM))

        self.detail_box = ctk.CTkTextbox(
            self,
            # 固定高度，与「执行日志」页同档：页签切换时整块坞不跳高，
            # 报告超出窗高时在窗内滚动，整块内容由最外层滚动条统一浏览。
            height=theme.DOCK_BOX_HEIGHT,
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.TEXT_PRIMARY,
            border_width=1,
            border_color=theme.EDGE,
            corner_radius=8,
        )
        self.detail_box.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_XS))
        # 体检明细通常超过一屏，滚动条要留在，但压成与窗同底的细轨
        tint_scrollbar(
            self.detail_box,
            track=theme.WELL,
            thumb=theme.EDGE,
            thumb_hover=theme.SIGNAL_DIM,
        )
        self._configure_tags()
        # 拦截滚轮事件，避免滚动详情时联动外层 CTkScrollableFrame 一起滚
        self._bind_scroll_block(self.detail_box)

    @staticmethod
    def _bind_scroll_block(widget: ctk.CTkTextbox) -> None:
        """绑定滚轮事件：手动滚动文本框并阻止向外传播，避免内外联动。"""

        def on_scroll(event) -> str:
            inner = getattr(widget, "_textbox", widget)
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

    def _configure_tags(self) -> None:
        # CTkTextbox 底层是 tkinter.Text，可以直接用 tag 上色。
        for verdict, color in VERDICT_COLORS.items():
            self.detail_box.tag_config(verdict.value, foreground=color)
        self.detail_box.tag_config("heading", foreground=theme.INFO)
        self.detail_box.tag_config("muted", foreground=theme.TEXT_MUTED)

    def reset(self) -> None:
        """清空面板，用于新一轮体检开始前。"""

        self._report_text = ""
        self.copy_btn.configure(state="disabled")
        self.verdict_label.configure(text="体检中", text_color=theme.IDLE)
        self.hint_label.configure(text="正在逐项探测，请稍候")
        self.counts_label.configure(text="")
        self.identity_label.configure(text=_IDENTITY_PLACEHOLDER)
        self.detail_box.delete("1.0", "end")

    def show_report(self, report: AuditReport) -> None:
        """渲染体检报告。"""

        self._report_text = report.to_text()
        self.copy_btn.configure(state="normal")

        verdict = report.summary_verdict
        counts = report.counts()
        self.verdict_label.configure(
            text=VERDICT_LABELS[verdict], text_color=VERDICT_COLORS[verdict]
        )
        self.hint_label.configure(text=_SUMMARY_HINTS[verdict])
        self.counts_label.configure(
            text=(
                f"异常 {counts[Verdict.FAIL]} · 可疑 {counts[Verdict.WARN]}"
                f" · 通过 {counts[Verdict.PASS]} · 跳过 {counts[Verdict.SKIP]}"
                f" · {report.call_count} 次调用 {report.total_latency_ms} ms"
            )
        )

        identity = report.identity
        self.identity_label.configure(
            text=(
                f"配置模型 {identity.configured_model or '—'}"
                f"（{identity.configured_source or '未知来源'}）"
                f"　│　实际回传 {identity.reported_model or '未回传'}"
                f"　│　模型自述 {identity.self_reported_text[:60] or '—'}"
            )
        )

        self.detail_box.delete("1.0", "end")
        if report.error_message:
            self._append(f"体检未完成：{report.error_message}\n\n", Verdict.FAIL.value)

        for category in (CATEGORY_IDENTITY, CATEGORY_PARAMS, CATEGORY_CAPABILITY):
            findings = report.by_category(category)
            if not findings:
                continue
            self._append(f"── {category} ──\n", "heading")
            for finding in findings:
                self._append(
                    f"[{VERDICT_LABELS[finding.verdict]}] ", finding.verdict.value
                )
                self._append(f"{finding.title}")
                self._append(
                    f"（置信度 {CONFIDENCE_LABELS[finding.confidence]}）\n", "muted"
                )
                if finding.detail:
                    for line in finding.detail.splitlines():
                        self._append(f"    {line}\n", "muted")
            self._append("\n")

        self.detail_box.see("1.0")

    def _append(self, text: str, tag: str | None = None) -> None:
        if tag:
            self.detail_box.insert("end", text, tag)
        else:
            self.detail_box.insert("end", text)

    def _copy_report(self) -> None:
        if self._report_text and self._on_copy:
            self._on_copy(self._report_text)
