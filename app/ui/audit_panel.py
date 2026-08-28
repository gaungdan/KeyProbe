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
from app.utils.textbox import fit_to_content, hide_scrollbar

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
            height=26,
            font=theme.font(12),
            state="disabled",
            command=self._copy_report,
        )
        self.copy_btn.pack(side="right")

        # 计数与身份核对各自成行：挤进一行会被裁切，而这两行都是要逐字读的
        self.counts_label = ctk.CTkLabel(
            self, text="", font=theme.mono(11), text_color=theme.TEXT_MUTED,
            justify="left", anchor="w",
        )
        self.counts_label.pack(fill="x", padx=theme.GAP_XS)

        self.identity_label = ctk.CTkLabel(
            self,
            text=_IDENTITY_PLACEHOLDER,
            font=theme.mono(11),
            text_color=theme.TEXT_FAINT,
            justify="left",
            anchor="w",
        )
        self.identity_label.pack(fill="x", padx=theme.GAP_XS, pady=(2, theme.GAP_SM))

        self.detail_box = ctk.CTkTextbox(
            self,
            height=120,
            font=theme.mono(12),
            fg_color=theme.WELL,
            text_color=theme.TEXT_PRIMARY,
            border_width=0,
        )
        # 不 expand：高度随报告内容撑开，交给外层滚动条统一滚动。
        self.detail_box.pack(fill="x", padx=theme.GAP_XS, pady=(0, theme.GAP_XS))
        hide_scrollbar(self.detail_box)
        self._configure_tags()

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
        fit_to_content(self.detail_box)

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
                f"异常 {counts[Verdict.FAIL]}   可疑 {counts[Verdict.WARN]}"
                f"   通过 {counts[Verdict.PASS]}   跳过 {counts[Verdict.SKIP]}"
                f"   ·   {report.call_count} 次调用 {report.total_latency_ms} ms"
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

        fit_to_content(self.detail_box)
        self.detail_box.see("1.0")

    def _append(self, text: str, tag: str | None = None) -> None:
        if tag:
            self.detail_box.insert("end", text, tag)
        else:
            self.detail_box.insert("end", text)

    def _copy_report(self) -> None:
        if self._report_text and self._on_copy:
            self._on_copy(self._report_text)
