"""模型体检核心：用一组探针回答四个问题。

1. 系统配置的是什么模型 —— 表单 / config.json / CC Switch 里写的模型名。
2. 实际调用的是什么模型 —— 响应体回传的模型标识，以及模型自述。
3. 是否具备预期能力 —— 工具调用、JSON 模式、视觉、长文本。
4. 调用参数是否被异常修改 —— max_tokens / stop / system / temperature 是否生效。

体检只发起最小化请求，每个探针的 max_tokens 都很小，深度档位也只有个位数调用。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Any

import httpx

from app.models.audit import (
    CATEGORY_CAPABILITY,
    CATEGORY_IDENTITY,
    CATEGORY_PARAMS,
    AuditFinding,
    AuditLevel,
    AuditReport,
    ModelIdentity,
    ProbeRecord,
    Verdict,
)
from app.models.config import UrlMode
from app.services.auth_headers import build_headers
from app.services.model_identity import (
    model_family,
    models_match,
    normalize_model_name,
    self_reported_family,
)
from app.services.protocol import (
    PROBE_TOOL_NAME,
    PROTOCOL_LABELS,
    ParsedProbe,
    ProbeSpec,
    build_probe_body,
    parse_probe_response,
    protocol_of,
    supports,
)
from app.services.providers import (
    OPENAI_COMPATIBLE,
    is_anthropic_provider,
    normalize_provider,
)
from app.services.url_builder import pick_test_url

LogCallback = Callable[[str], None]

# 探针提示词。刻意写得短，避免额外 token 消耗。
IDENTITY_PROMPT = (
    "Answer with only the exact name of the AI model that is generating this reply. "
    "No punctuation, no explanation."
)
LONG_OUTPUT_PROMPT = "List the numbers from 1 to 60 separated by commas."
SYSTEM_DIRECTIVE = (
    "You must reply with exactly the single token KEYPROBE-OK and nothing else, "
    "whatever the user asks."
)
SYSTEM_DIRECTIVE_MARKER = "KEYPROBE-OK"
SYSTEM_DIRECTIVE_PROMPT = "What is the capital of France?"
STOP_PROMPT = "Output exactly this text: ALPHA BRAVO CHARLIE DELTA"
STOP_SEQUENCE = "CHARLIE"
STOP_TAIL_MARKER = "DELTA"
TOOL_PROMPT = "The user is currently in Tokyo. Report the city using the available tool."
JSON_PROMPT = 'Return a JSON object with the single key "ok" whose value is true.'
IMAGE_PROMPT = "Reply with the single word IMAGE."
DETERMINISM_PROMPT = "Write one short random English sentence about the ocean."

# max_tokens 探针的额度。取 8 是因为几乎所有模型都会因此被截断，
# 便于观察 max_tokens 是否真的生效。
MAX_TOKENS_PROBE_LIMIT = 8
# 网关计费口径、思考 token、多字节切分都可能让实际输出略微超出，留 3 倍余量再判异常。
MAX_TOKENS_TOLERANCE = 3

# 表示「因达到长度上限而结束」的结束原因（各协议写法不同）。
LENGTH_FINISH_REASONS = {
    "length",
    "max_tokens",
    "max_output_tokens",
    "maxtokens",
    "incomplete",
    "model_length",
    "token_limit",
}


class ModelAuditor:
    """对当前配置执行模型体检。"""

    def __init__(
        self, log: LogCallback | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._log = log or (lambda message: None)
        self._transport = transport

    async def audit(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        timeout: int,
        url_mode: UrlMode,
        base_url: str,
        level: AuditLevel = "standard",
        known_models: Sequence[str] | None = None,
        configured_source: str = "",
    ) -> AuditReport:
        """执行体检并返回报告。"""

        effective_provider = normalize_provider(provider)
        protocol = protocol_of(effective_provider)
        report = AuditReport(
            level=level,
            provider=effective_provider,
            protocol=PROTOCOL_LABELS.get(protocol, protocol),
            base_url=base_url.strip(),
        )

        if not api_key.strip():
            report.error_message = "API Key 不能为空"
            return report
        if not model.strip():
            report.error_message = "请先选择或填写模型"
            return report

        url = pick_test_url(effective_provider, base_url, model, url_mode)
        if not url:
            report.error_message = "测试接口 URL 不能为空"
            return report
        report.request_url = url

        report.identity = ModelIdentity(
            configured_model=model.strip(),
            configured_source=configured_source or "当前表单",
            configured_family=model_family(model),
            listed_in_model_list=self._listed(model, known_models),
        )

        self._log(
            f"体检目标：{url}｜协议 {report.protocol}｜配置模型 {model.strip()}"
        )

        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            context = _AuditContext(
                client=client,
                report=report,
                protocol=protocol,
                provider=effective_provider,
                api_key=api_key,
                model=model.strip(),
                url=url,
            )

            identity_probe = await self._probe(
                context,
                key="identity",
                title="模型身份与自述",
                spec=ProbeSpec(user=IDENTITY_PROMPT, max_tokens=48, temperature=0.0),
            )

            # Anthropic 鉴权在很多中转网关上会失败（它们要的是 Authorization: Bearer，
            # 而原生 Anthropic 用 x-api-key）。与「测试接口」一致：401/403 时切到
            # OpenAI 兼容鉴权重试，保留 Anthropic 请求格式，后续探针沿用该鉴权方式。
            if (
                identity_probe
                and not identity_probe.ok
                and identity_probe.record.status_code in {401, 403}
                and is_anthropic_provider(context.provider)
                and context.auth_provider != OPENAI_COMPATIBLE
            ):
                self._log("Anthropic 鉴权失败，尝试 OpenAI 兼容鉴权（Bearer）重试…")
                context.auth_provider = OPENAI_COMPATIBLE
                identity_probe = await self._probe(
                    context,
                    key="identity",
                    title="模型身份与自述（Bearer 重试）",
                    spec=ProbeSpec(user=IDENTITY_PROMPT, max_tokens=48, temperature=0.0),
                )

            if identity_probe is None or identity_probe.record.error_message:
                report.error_message = (
                    identity_probe.record.error_message
                    if identity_probe
                    else "体检请求失败"
                )
                if identity_probe is not None and identity_probe.record.status_code:
                    # 鉴权/额度问题已经由「测试接口」覆盖，这里直接结束体检。
                    self._log("首个探针失败，终止体检")
                return report

            self._apply_identity(report, identity_probe)

            max_tokens_probe = await self._probe(
                context,
                key="max_tokens",
                title="max_tokens 是否生效",
                spec=ProbeSpec(
                    user=LONG_OUTPUT_PROMPT,
                    max_tokens=MAX_TOKENS_PROBE_LIMIT,
                    temperature=0.0,
                ),
            )
            self._apply_max_tokens(report, max_tokens_probe)

            if level in {"standard", "deep"}:
                system_probe = await self._probe(
                    context,
                    key="system",
                    title="system 指令是否生效",
                    spec=ProbeSpec(
                        user=SYSTEM_DIRECTIVE_PROMPT,
                        system=SYSTEM_DIRECTIVE,
                        max_tokens=24,
                        temperature=0.0,
                    ),
                )
                self._apply_system(report, system_probe)

                if supports(protocol, "stop"):
                    stop_probe = await self._probe(
                        context,
                        key="stop",
                        title="stop 序列是否生效",
                        spec=ProbeSpec(
                            user=STOP_PROMPT,
                            max_tokens=32,
                            temperature=0.0,
                            stop=[STOP_SEQUENCE],
                        ),
                    )
                    self._apply_stop(report, stop_probe)
                else:
                    report.findings.append(
                        AuditFinding(
                            key="stop",
                            category=CATEGORY_PARAMS,
                            title="stop 序列是否生效",
                            verdict=Verdict.SKIP,
                            detail=f"{report.protocol} 没有 stop 参数，已跳过。",
                        )
                    )

                tool_probe = await self._probe(
                    context,
                    key="tools",
                    title="工具调用（function calling）",
                    spec=ProbeSpec(
                        user=TOOL_PROMPT, max_tokens=128, temperature=0.0, tools=True
                    ),
                )
                self._apply_tools(report, tool_probe)

            if level == "deep":
                if supports(protocol, "json_mode"):
                    json_probe = await self._probe(
                        context,
                        key="json_mode",
                        title="JSON 结构化输出",
                        spec=ProbeSpec(
                            user=JSON_PROMPT,
                            max_tokens=64,
                            temperature=0.0,
                            json_mode=True,
                        ),
                    )
                    self._apply_json_mode(report, json_probe)
                else:
                    report.findings.append(
                        AuditFinding(
                            key="json_mode",
                            category=CATEGORY_CAPABILITY,
                            title="JSON 结构化输出",
                            verdict=Verdict.SKIP,
                            detail=f"{report.protocol} 未定义 JSON 模式参数，已跳过。",
                        )
                    )

                image_probe = await self._probe(
                    context,
                    key="vision",
                    title="图像输入（视觉）",
                    spec=ProbeSpec(
                        user=IMAGE_PROMPT, max_tokens=16, temperature=0.0, image=True
                    ),
                )
                self._apply_vision(report, image_probe)

                first = await self._probe(
                    context,
                    key="determinism_1",
                    title="temperature=0 复现性（第 1 次）",
                    spec=ProbeSpec(user=DETERMINISM_PROMPT, max_tokens=40, temperature=0.0),
                )
                second = await self._probe(
                    context,
                    key="determinism_2",
                    title="temperature=0 复现性（第 2 次）",
                    spec=ProbeSpec(user=DETERMINISM_PROMPT, max_tokens=40, temperature=0.0),
                )
                self._apply_determinism(report, first, second)

        self._apply_route_stability(report)
        self._log(
            f"体检结束：共 {report.call_count} 次调用，"
            f"发现 {len(report.problems())} 项需要关注"
        )
        return report

    # ── 探针执行 ────────────────────────────────────────────────────────

    async def _probe(
        self, context: _AuditContext, *, key: str, title: str, spec: ProbeSpec
    ) -> _ProbeOutcome | None:
        body = build_probe_body(context.protocol, context.model, spec)
        # 鉴权方式可能被 Anthropic→OpenAI 兼容回退改写过，以 context.auth_provider 为准；
        # 请求体仍按 context.protocol（Anthropic Messages 等原生格式）构造。
        headers = build_headers(context.auth_provider, context.api_key, "POST", context.url, body)
        record = ProbeRecord(key=key, title=title, url=context.url, request_body=body)

        self._log(f"探针「{title}」→ POST {context.url}")
        started = perf_counter()
        try:
            response = await context.client.post(context.url, headers=headers, json=body)
        except httpx.RequestError as exc:
            record.latency_ms = int((perf_counter() - started) * 1000)
            record.error_message = f"网络错误：{exc}"
            self._log(record.error_message)
            context.report.probes.append(record)
            context.report.call_count += 1
            return _ProbeOutcome(record=record, parsed=ParsedProbe(), payload={}, ok=False)

        record.latency_ms = int((perf_counter() - started) * 1000)
        record.status_code = response.status_code
        payload = _safe_json(response)
        context.report.call_count += 1
        context.report.total_latency_ms += record.latency_ms

        if not 200 <= response.status_code < 300:
            record.error_message = _extract_error(response, payload)
            self._log(
                f"探针「{title}」失败：HTTP {response.status_code} {record.error_message}"
            )
            context.report.probes.append(record)
            return _ProbeOutcome(record=record, parsed=ParsedProbe(), payload=payload, ok=False)

        parsed = parse_probe_response(context.protocol, payload)
        record.reported_model = parsed.reported_model
        record.finish_reason = parsed.finish_reason
        record.output_text = parsed.text[:400]
        record.input_tokens = parsed.input_tokens
        record.output_tokens = parsed.output_tokens
        record.tool_calls = list(parsed.tool_calls)
        context.report.probes.append(record)
        self._log(
            f"探针「{title}」完成：{record.latency_ms} ms"
            f"｜回传模型 {parsed.reported_model or '—'}"
            f"｜finish={parsed.finish_reason or '—'}"
            f"｜输出 token {parsed.output_tokens}"
        )
        return _ProbeOutcome(record=record, parsed=parsed, payload=payload, ok=True)

    # ── 结论判定 ────────────────────────────────────────────────────────

    @staticmethod
    def _is_rate_limited(probe: _ProbeOutcome | None) -> bool:
        """探针是否因 429 限流而失败。"""

        return probe is not None and probe.record.status_code == 429

    def _add_rate_limited(
        self, report: AuditReport, *, key: str, title: str, category: str
    ) -> None:
        """追加一条限流结论：不判为异常，提示稍后重试。"""

        report.findings.append(
            AuditFinding(
                key=key,
                category=category,
                title=title,
                verdict=Verdict.RATE_LIMITED,
                detail=(
                    "该探针因服务端速率/TPM 限制（HTTP 429）未完成，"
                    "不判为异常，建议降低体检深度或稍后重试。"
                ),
                confidence="high",
                evidence={"status_code": 429},
            )
        )

    @staticmethod
    def _listed(model: str, known_models: Sequence[str] | None) -> bool | None:
        if not known_models:
            return None
        target = normalize_model_name(model)
        return any(normalize_model_name(item) == target for item in known_models)

    def _apply_identity(self, report: AuditReport, probe: _ProbeOutcome) -> None:
        identity = report.identity
        identity.reported_model = probe.parsed.reported_model
        identity.reported_family = model_family(probe.parsed.reported_model)
        identity.self_reported_text = probe.parsed.text[:200]
        identity.self_reported_family = self_reported_family(probe.parsed.text)

        protocol_reports_model = supports(
            protocol_of(report.provider), "reported_model"
        )

        # 服务端回传模型是否与配置一致；自述项据此降级（身份已确认时，自述不一致更可能是幻觉）。
        reported_match = bool(identity.reported_model) and models_match(
            identity.configured_model, identity.reported_model
        )

        # 1) 配置模型 vs 服务端回传模型
        if not identity.reported_model:
            report.findings.append(
                AuditFinding(
                    key="reported_model",
                    category=CATEGORY_IDENTITY,
                    title="服务端回传模型标识",
                    verdict=Verdict.SKIP if not protocol_reports_model else Verdict.WARN,
                    detail=(
                        f"{report.protocol} 响应体不包含模型标识，无法核对。"
                        if not protocol_reports_model
                        else "响应体没有回传模型标识，无法确认实际调用的模型，"
                        "常见于二次封装的中转网关。"
                    ),
                    confidence="high" if not protocol_reports_model else "medium",
                    evidence={"configured": identity.configured_model},
                )
            )
        elif models_match(identity.configured_model, identity.reported_model):
            report.findings.append(
                AuditFinding(
                    key="reported_model",
                    category=CATEGORY_IDENTITY,
                    title="服务端回传模型标识",
                    verdict=Verdict.PASS,
                    detail=(
                        f"配置 {identity.configured_model}，"
                        f"服务端回传 {identity.reported_model}，指向同一模型。"
                    ),
                    evidence={
                        "configured": identity.configured_model,
                        "reported": identity.reported_model,
                    },
                )
            )
        else:
            same_family = bool(
                identity.configured_family
                and identity.configured_family == identity.reported_family
            )
            report.findings.append(
                AuditFinding(
                    key="reported_model",
                    category=CATEGORY_IDENTITY,
                    title="服务端回传模型标识",
                    verdict=Verdict.WARN if same_family else Verdict.FAIL,
                    detail=(
                        f"配置 {identity.configured_model}，"
                        f"服务端实际回传 {identity.reported_model}。"
                        + (
                            "同族系但版本/档位不同，可能被网关降级或改写。"
                            if same_family
                            else "族系都不一致，请求很可能被转发到了别的模型。"
                        )
                    ),
                    evidence={
                        "configured": identity.configured_model,
                        "reported": identity.reported_model,
                    },
                )
            )

        # 2) 模型自述 vs 配置模型
        if not identity.configured_family:
            report.findings.append(
                AuditFinding(
                    key="self_report",
                    category=CATEGORY_IDENTITY,
                    title="模型自述身份",
                    verdict=Verdict.SKIP,
                    detail=f"无法从模型名 {identity.configured_model} 判断族系，跳过自述比对。",
                    evidence={"self_reported": identity.self_reported_text},
                )
            )
        elif not identity.self_reported_family:
            report.findings.append(
                AuditFinding(
                    key="self_report",
                    category=CATEGORY_IDENTITY,
                    title="模型自述身份",
                    verdict=Verdict.UNKNOWN,
                    detail=(
                        "模型没有明确说出自己是谁，无法作为身份证据。回答："
                        f"{identity.self_reported_text or '（空）'}"
                    ),
                    confidence="low",
                    evidence={"self_reported": identity.self_reported_text},
                )
            )
        elif identity.self_reported_family == identity.configured_family:
            report.findings.append(
                AuditFinding(
                    key="self_report",
                    category=CATEGORY_IDENTITY,
                    title="模型自述身份",
                    verdict=Verdict.PASS,
                    detail=(
                        f"模型自述与配置族系一致（{identity.configured_family}）。回答："
                        f"{identity.self_reported_text}"
                    ),
                    confidence="medium",
                    evidence={"self_reported": identity.self_reported_text},
                )
            )
        else:
            report.findings.append(
                AuditFinding(
                    key="self_report",
                    category=CATEGORY_IDENTITY,
                    title="模型自述身份",
                    verdict=Verdict.WARN if reported_match else Verdict.FAIL,
                    detail=(
                        (
                            f"服务端回传模型已与配置一致（{identity.reported_model}），"
                            f"但模型自述为 {identity.self_reported_family}，"
                            "很可能是训练数据导致的幻觉，仅供参考。回答："
                            if reported_match
                            else f"配置族系为 {identity.configured_family}，"
                            f"但模型自述为 {identity.self_reported_family}。回答："
                        )
                        + f"{identity.self_reported_text}"
                    ),
                    confidence="low" if reported_match else "medium",
                    evidence={"self_reported": identity.self_reported_text},
                )
            )

        # 3) 模型是否出现在模型列表里
        if identity.listed_in_model_list is None:
            report.findings.append(
                AuditFinding(
                    key="model_listed",
                    category=CATEGORY_IDENTITY,
                    title="模型是否在服务商模型列表中",
                    verdict=Verdict.SKIP,
                    detail="尚未获取模型列表，跳过该项。先点「获取模型」可启用。",
                )
            )
        else:
            report.findings.append(
                AuditFinding(
                    key="model_listed",
                    category=CATEGORY_IDENTITY,
                    title="模型是否在服务商模型列表中",
                    verdict=Verdict.PASS if identity.listed_in_model_list else Verdict.WARN,
                    detail=(
                        "模型出现在服务商返回的模型列表中。"
                        if identity.listed_in_model_list
                        else "模型列表里没有这个模型名，服务端可能做了别名映射或该模型未开放。"
                    ),
                    confidence="high" if identity.listed_in_model_list else "medium",
                )
            )

    def _apply_max_tokens(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "max_tokens 上限是否被遵守"
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="max_tokens", title=title, category=CATEGORY_PARAMS)
            return
        if probe is None or not probe.ok:
            report.findings.append(
                AuditFinding(
                    key="max_tokens",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.UNKNOWN,
                    detail=_failure_detail(probe),
                    confidence="low",
                )
            )
            return

        limit = MAX_TOKENS_PROBE_LIMIT
        output_tokens = probe.parsed.output_tokens
        estimated = False
        if output_tokens is None:
            # 没有 usage 就按 4 字符 ≈ 1 token 粗估，结论降为中等置信度。
            output_tokens = max(1, len(probe.parsed.text) // 4)
            estimated = True

        finish = probe.parsed.finish_reason.lower().replace("-", "_")
        truncated = finish in LENGTH_FINISH_REASONS

        if output_tokens > limit * MAX_TOKENS_TOLERANCE:
            report.findings.append(
                AuditFinding(
                    key="max_tokens",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.FAIL,
                    detail=(
                        f"请求 max_tokens={limit}，实际输出约 {output_tokens} token"
                        f"（{'按文本长度估算' if estimated else '来自 usage'}），"
                        "上限明显未生效，参数可能被网关丢弃或覆盖。"
                    ),
                    confidence="medium" if estimated else "high",
                    evidence={"limit": limit, "output_tokens": output_tokens},
                )
            )
            return

        if truncated or output_tokens <= limit:
            report.findings.append(
                AuditFinding(
                    key="max_tokens",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.PASS,
                    detail=(
                        f"请求 max_tokens={limit}，输出约 {output_tokens} token，"
                        f"结束原因 {probe.parsed.finish_reason or '未回传'}，上限生效。"
                    ),
                    confidence="medium" if estimated else "high",
                    evidence={
                        "limit": limit,
                        "output_tokens": output_tokens,
                        "finish_reason": probe.parsed.finish_reason,
                    },
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="max_tokens",
                category=CATEGORY_PARAMS,
                title=title,
                verdict=Verdict.WARN,
                detail=(
                    f"请求 max_tokens={limit}，输出约 {output_tokens} token 且未标记截断"
                    f"（结束原因 {probe.parsed.finish_reason or '未回传'}），略微超限，"
                    "可能是计费口径差异，也可能上限被放宽。"
                ),
                confidence="low" if estimated else "medium",
                evidence={"limit": limit, "output_tokens": output_tokens},
            )
        )

    def _apply_system(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "system 指令是否生效"
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="system", title=title, category=CATEGORY_PARAMS)
            return
        if probe is None or not probe.ok:
            report.findings.append(
                AuditFinding(
                    key="system",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.UNKNOWN,
                    detail=_failure_detail(probe),
                    confidence="low",
                )
            )
            return

        text = probe.parsed.text
        if SYSTEM_DIRECTIVE_MARKER in text.upper():
            report.findings.append(
                AuditFinding(
                    key="system",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.PASS,
                    detail="模型遵守了 system 指令，说明 system 提示被完整透传。",
                    evidence={"output": text[:120]},
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="system",
                category=CATEGORY_PARAMS,
                title=title,
                verdict=Verdict.WARN,
                detail=(
                    "模型没有按 system 指令回答，system 提示可能被网关丢弃、"
                    f"改写或追加了额外内容。回答：{text[:120] or '（空）'}"
                ),
                confidence="medium",
                evidence={"output": text[:120]},
            )
        )

    def _apply_stop(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "stop 序列是否生效"
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="stop", title=title, category=CATEGORY_PARAMS)
            return
        if probe is None or not probe.ok:
            report.findings.append(
                AuditFinding(
                    key="stop",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.UNKNOWN,
                    detail=_failure_detail(probe),
                    confidence="low",
                )
            )
            return

        upper = probe.parsed.text.upper()
        leaked = STOP_TAIL_MARKER in upper
        report.findings.append(
            AuditFinding(
                key="stop",
                category=CATEGORY_PARAMS,
                title=title,
                verdict=Verdict.WARN if leaked else Verdict.PASS,
                detail=(
                    f"请求以 {STOP_SEQUENCE} 为 stop 序列，输出仍包含其后的 "
                    f"{STOP_TAIL_MARKER}，stop 参数疑似未透传。"
                    if leaked
                    else f"输出在 stop 序列 {STOP_SEQUENCE} 处被截断，参数生效。"
                ),
                confidence="medium",
                evidence={"output": probe.parsed.text[:120]},
            )
        )

    def _apply_tools(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "工具调用（function calling）"
        if probe is None:
            return
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="tools", title=title, category=CATEGORY_CAPABILITY)
            return
        if not probe.ok:
            report.findings.append(
                AuditFinding(
                    key="tools",
                    category=CATEGORY_CAPABILITY,
                    title=title,
                    verdict=Verdict.FAIL,
                    detail=(
                        "带工具定义的请求被拒绝，该模型或网关不具备工具调用能力："
                        + _failure_detail(probe)
                    ),
                    evidence={"status_code": probe.record.status_code},
                )
            )
            return

        if PROBE_TOOL_NAME in probe.parsed.tool_calls:
            report.findings.append(
                AuditFinding(
                    key="tools",
                    category=CATEGORY_CAPABILITY,
                    title=title,
                    verdict=Verdict.PASS,
                    detail=f"模型按要求发起了工具调用 {PROBE_TOOL_NAME}。",
                    evidence={"tool_calls": probe.parsed.tool_calls},
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="tools",
                category=CATEGORY_CAPABILITY,
                title=title,
                verdict=Verdict.WARN,
                detail=(
                    "请求已强制要求调用工具，但响应里没有工具调用，"
                    f"工具能力可能被网关剥离。回答：{probe.parsed.text[:120] or '（空）'}"
                ),
                confidence="medium",
                evidence={"tool_calls": probe.parsed.tool_calls},
            )
        )

    def _apply_json_mode(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "JSON 结构化输出"
        if probe is None:
            return
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="json_mode", title=title, category=CATEGORY_CAPABILITY)
            return
        if not probe.ok:
            report.findings.append(
                AuditFinding(
                    key="json_mode",
                    category=CATEGORY_CAPABILITY,
                    title=title,
                    verdict=Verdict.FAIL,
                    detail="JSON 模式请求被拒绝：" + _failure_detail(probe),
                )
            )
            return

        if _is_json_object(probe.parsed.text):
            report.findings.append(
                AuditFinding(
                    key="json_mode",
                    category=CATEGORY_CAPABILITY,
                    title=title,
                    verdict=Verdict.PASS,
                    detail="JSON 模式下返回了合法 JSON 对象。",
                    evidence={"output": probe.parsed.text[:120]},
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="json_mode",
                category=CATEGORY_CAPABILITY,
                title=title,
                verdict=Verdict.WARN,
                detail=(
                    "请求了 JSON 模式，但返回内容不是合法 JSON 对象，"
                    f"该参数可能未生效。输出：{probe.parsed.text[:120] or '（空）'}"
                ),
                confidence="medium",
                evidence={"output": probe.parsed.text[:120]},
            )
        )

    def _apply_vision(self, report: AuditReport, probe: _ProbeOutcome | None) -> None:
        title = "图像输入（视觉）"
        if probe is None:
            return
        if self._is_rate_limited(probe):
            self._add_rate_limited(report, key="vision", title=title, category=CATEGORY_CAPABILITY)
            return
        if probe.ok:
            report.findings.append(
                AuditFinding(
                    key="vision",
                    category=CATEGORY_CAPABILITY,
                    title=title,
                    verdict=Verdict.PASS,
                    detail="携带内联图片的请求被正常受理，具备图像输入能力。",
                    confidence="medium",
                    evidence={"output": probe.parsed.text[:120]},
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="vision",
                category=CATEGORY_CAPABILITY,
                title=title,
                verdict=Verdict.FAIL if probe.record.status_code else Verdict.UNKNOWN,
                detail="携带图片的请求被拒绝，不具备图像输入能力：" + _failure_detail(probe),
                confidence="medium",
                evidence={"status_code": probe.record.status_code},
            )
        )

    def _apply_determinism(
        self,
        report: AuditReport,
        first: _ProbeOutcome | None,
        second: _ProbeOutcome | None,
    ) -> None:
        title = "temperature=0 复现性"
        if self._is_rate_limited(first) or self._is_rate_limited(second):
            self._add_rate_limited(report, key="determinism", title=title, category=CATEGORY_PARAMS)
            return
        if first is None or second is None or not first.ok or not second.ok:
            report.findings.append(
                AuditFinding(
                    key="determinism",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.UNKNOWN,
                    detail=_failure_detail(first if first and not first.ok else second),
                    confidence="low",
                )
            )
            return

        left = _normalize_text(first.parsed.text)
        right = _normalize_text(second.parsed.text)
        if not left or not right:
            report.findings.append(
                AuditFinding(
                    key="determinism",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.UNKNOWN,
                    detail="两次调用中有一次没有返回文本，无法比较复现性。",
                    confidence="low",
                )
            )
            return

        if left == right:
            report.findings.append(
                AuditFinding(
                    key="determinism",
                    category=CATEGORY_PARAMS,
                    title=title,
                    verdict=Verdict.PASS,
                    detail="temperature=0 时两次输出完全一致，采样参数看起来被遵守。",
                    confidence="medium",
                )
            )
            return

        report.findings.append(
            AuditFinding(
                key="determinism",
                category=CATEGORY_PARAMS,
                title=title,
                verdict=Verdict.WARN,
                detail=(
                    "temperature=0 时两次输出不同。部分模型/推理后端本身不保证确定性，"
                    "但也可能是 temperature 被网关覆盖。\n"
                    f"第 1 次：{first.parsed.text[:80]}\n第 2 次：{second.parsed.text[:80]}"
                ),
                confidence="low",
            )
        )

    def _apply_route_stability(self, report: AuditReport) -> None:
        reported = {
            probe.reported_model for probe in report.probes if probe.reported_model
        }
        if len(reported) <= 1:
            return
        report.findings.append(
            AuditFinding(
                key="route_stability",
                category=CATEGORY_IDENTITY,
                title="多次调用回传模型是否一致",
                verdict=Verdict.FAIL,
                detail=(
                    "同一份配置在多次调用中回传了不同模型："
                    + "、".join(sorted(reported))
                    + "，说明服务端存在动态路由或降级。"
                ),
                evidence={"reported_models": sorted(reported)},
            )
        )


class _AuditContext:
    """探针执行期间共享的连接与配置。"""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        report: AuditReport,
        protocol: str,
        provider: str,
        api_key: str,
        model: str,
        url: str,
        auth_provider: str = "",
    ) -> None:
        self.client = client
        self.report = report
        self.protocol = protocol
        self.provider = provider
        # 鉴权方式，默认与 provider 一致；Anthropic 401 时回退到 OpenAI 兼容 Bearer。
        self.auth_provider = auth_provider or provider
        self.api_key = api_key
        self.model = model
        self.url = url


class _ProbeOutcome:
    """一次探针的执行结果。"""

    def __init__(
        self, *, record: ProbeRecord, parsed: ParsedProbe, payload: Any, ok: bool
    ) -> None:
        self.record = record
        self.parsed = parsed
        self.payload = payload
        self.ok = ok


def _failure_detail(probe: _ProbeOutcome | None) -> str:
    if probe is None:
        return "探针未执行。"
    if probe.record.error_message:
        return f"HTTP {probe.record.status_code or '—'} {probe.record.error_message}"
    return f"HTTP {probe.record.status_code or '—'}"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_json_object(text: str) -> bool:
    stripped = (text or "").strip()
    # 部分模型会用 ```json 包裹，先剥掉代码围栏。
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    if not stripped.startswith("{"):
        return False
    try:
        return isinstance(json.loads(stripped), dict)
    except ValueError:
        return False


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {}


def _extract_error(response: httpx.Response, payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or "")[:300]
        if isinstance(error, str):
            return error[:300]
        for key in ("message", "Message", "detail"):
            if payload.get(key):
                return str(payload[key])[:300]
    return (response.text[:300] or response.reason_phrase).strip()
