"""模型体检（Model Audit）数据模型。

体检回答四个问题：

1. 系统配置的是什么模型（configured）——来自 config.json / CC Switch / 手动输入。
2. 实际调用的是什么模型（reported / self-reported）——来自响应体的模型标识与模型自述。
3. 这个模型是否具备预期能力（capabilities）——工具调用、JSON 模式、视觉、流式等。
4. 实际调用参数是否被异常修改（parameters）——max_tokens / temperature / stop /
   system 提示是否被网关忽略、覆盖或注入。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.config import AuditLevel

AUDIT_LEVEL_LABELS: dict[AuditLevel, str] = {
    "quick": "快速（约 2 次调用）",
    "standard": "标准（约 5 次调用）",
    "deep": "深度（约 9 次调用）",
}

AUDIT_LEVEL_BY_LABEL: dict[str, AuditLevel] = {
    label: level for level, label in AUDIT_LEVEL_LABELS.items()
}


class Verdict(str, Enum):
    """单项体检结论。"""

    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.PASS: "通过",
    Verdict.WARN: "可疑",
    Verdict.FAIL: "异常",
    Verdict.SKIP: "跳过",
    Verdict.RATE_LIMITED: "限流",
    Verdict.UNKNOWN: "未知",
}

VERDICT_COLORS: dict[Verdict, str] = {
    Verdict.PASS: "#22c55e",
    Verdict.WARN: "#f59e0b",
    Verdict.FAIL: "#ef4444",
    Verdict.SKIP: "#6b7280",
    Verdict.RATE_LIMITED: "#3b82f6",
    Verdict.UNKNOWN: "#9ca3af",
}

# 结论严重度，用于汇总总评。限流不算问题（不是能力/参数故障），但应高于通过，以便在总评里体现。
VERDICT_SEVERITY: dict[Verdict, int] = {
    Verdict.SKIP: 0,
    Verdict.PASS: 1,
    Verdict.RATE_LIMITED: 2,
    Verdict.UNKNOWN: 2,
    Verdict.WARN: 3,
    Verdict.FAIL: 4,
}

Confidence = Literal["high", "medium", "low"]

CONFIDENCE_LABELS: dict[Confidence, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

# 体检维度
CATEGORY_IDENTITY = "模型身份"
CATEGORY_PARAMS = "调用参数"
CATEGORY_CAPABILITY = "模型能力"


class AuditFinding(BaseModel):
    """一条体检结论。"""

    key: str
    category: str
    title: str
    verdict: Verdict = Verdict.UNKNOWN
    detail: str = ""
    confidence: Confidence = "high"
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_problem(self) -> bool:
        """是否属于需要关注的结论。"""

        return self.verdict in {Verdict.WARN, Verdict.FAIL}


class ProbeRecord(BaseModel):
    """一次探针调用的留痕，便于复核实际发出的参数。"""

    key: str
    title: str
    url: str = ""
    status_code: int = 0
    latency_ms: int | None = None
    request_body: dict[str, Any] = Field(default_factory=dict)
    reported_model: str = ""
    finish_reason: str = ""
    output_text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[str] = Field(default_factory=list)
    error_message: str = ""


class ModelIdentity(BaseModel):
    """模型身份三方对照。"""

    configured_model: str = ""
    configured_source: str = ""
    reported_model: str = ""
    self_reported_text: str = ""
    configured_family: str = ""
    reported_family: str = ""
    self_reported_family: str = ""
    listed_in_model_list: bool | None = None


class AuditReport(BaseModel):
    """完整体检报告。"""

    level: AuditLevel = "standard"
    provider: str = ""
    protocol: str = ""
    base_url: str = ""
    request_url: str = ""
    identity: ModelIdentity = Field(default_factory=ModelIdentity)
    findings: list[AuditFinding] = Field(default_factory=list)
    probes: list[ProbeRecord] = Field(default_factory=list)
    call_count: int = 0
    total_latency_ms: int = 0
    error_message: str = ""

    @property
    def summary_verdict(self) -> Verdict:
        """取最严重的一项作为总评。"""

        if self.error_message and not self.findings:
            return Verdict.UNKNOWN
        if not self.findings:
            return Verdict.UNKNOWN
        return max(
            (finding.verdict for finding in self.findings),
            key=lambda verdict: VERDICT_SEVERITY[verdict],
        )

    def by_category(self, category: str) -> list[AuditFinding]:
        """按维度取结论。"""

        return [finding for finding in self.findings if finding.category == category]

    def problems(self) -> list[AuditFinding]:
        """取所有需要关注的结论。"""

        return [finding for finding in self.findings if finding.is_problem]

    def counts(self) -> dict[Verdict, int]:
        """各结论数量。"""

        result = {verdict: 0 for verdict in Verdict}
        for finding in self.findings:
            result[finding.verdict] += 1
        return result

    def to_text(self) -> str:
        """渲染为可复制的纯文本报告。"""

        lines: list[str] = []
        lines.append("========== KeyProbe 模型体检报告 ==========")
        lines.append(f"体检深度：{AUDIT_LEVEL_LABELS.get(self.level, self.level)}")
        lines.append(f"服务商/协议：{self.provider}（{self.protocol}）")
        lines.append(f"接口地址：{self.base_url}")
        if self.request_url:
            lines.append(f"实际请求：{self.request_url}")
        lines.append(
            f"总评：{VERDICT_LABELS[self.summary_verdict]}"
            f"　调用次数：{self.call_count}　累计耗时：{self.total_latency_ms} ms"
        )
        if self.error_message:
            lines.append(f"错误：{self.error_message}")

        identity = self.identity
        lines.append("")
        lines.append("---------- 模型身份 ----------")
        lines.append(
            f"配置模型：{identity.configured_model or '—'}"
            f"（来源：{identity.configured_source or '未知'}"
            f"，族系：{identity.configured_family or '未知'}）"
        )
        lines.append(
            f"服务端回传模型：{identity.reported_model or '（未回传）'}"
            f"（族系：{identity.reported_family or '未知'}）"
        )
        if identity.self_reported_text:
            lines.append(
                f"模型自述：{identity.self_reported_text}"
                f"（族系：{identity.self_reported_family or '未识别'}）"
            )
        if identity.listed_in_model_list is not None:
            lines.append(
                "模型列表包含该模型：" + ("是" if identity.listed_in_model_list else "否")
            )

        for category in (CATEGORY_IDENTITY, CATEGORY_PARAMS, CATEGORY_CAPABILITY):
            findings = self.by_category(category)
            if not findings:
                continue
            lines.append("")
            lines.append(f"---------- {category} ----------")
            for finding in findings:
                lines.append(
                    f"[{VERDICT_LABELS[finding.verdict]}] {finding.title}"
                    f"（置信度：{CONFIDENCE_LABELS[finding.confidence]}）"
                )
                if finding.detail:
                    lines.append(f"    {finding.detail}")

        if self.probes:
            lines.append("")
            lines.append("---------- 探针留痕 ----------")
            for probe in self.probes:
                lines.append(
                    f"{probe.title}｜HTTP {probe.status_code}｜{probe.latency_ms} ms"
                    f"｜回传模型 {probe.reported_model or '—'}"
                    f"｜finish={probe.finish_reason or '—'}"
                    f"｜tokens in/out={probe.input_tokens}/{probe.output_tokens}"
                )
                if probe.error_message:
                    lines.append(f"    错误：{probe.error_message}")
        return "\n".join(lines)
