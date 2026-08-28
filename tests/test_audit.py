"""模型体检（ModelAuditor）测试：用 MockTransport 模拟各种网关异常行为。"""

import asyncio
import json

import httpx

from app.checker.auditor import (
    MAX_TOKENS_PROBE_LIMIT,
    STOP_SEQUENCE,
    STOP_TAIL_MARKER,
    SYSTEM_DIRECTIVE_MARKER,
    ModelAuditor,
)
from app.models.audit import (
    CATEGORY_CAPABILITY,
    CATEGORY_IDENTITY,
    CATEGORY_PARAMS,
    Verdict,
)
from app.services.model_identity import (
    model_family,
    models_match,
    normalize_model_name,
    self_reported_family,
)
from app.services.protocol import (
    PROBE_TOOL_NAME,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_BEDROCK,
    PROTOCOL_GEMINI,
    PROTOCOL_OPENAI_CHAT,
    PROTOCOL_OPENAI_RESPONSES,
    ProbeSpec,
    build_probe_body,
    parse_probe_response,
    protocol_of,
    supports,
)
from app.services.providers import (
    AMAZON_BEDROCK,
    ANTHROPIC,
    GOOGLE_GEMINI,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
)


# ── 辅助 ────────────────────────────────────────────────────────────────


def _finding(report, key):
    return next(finding for finding in report.findings if finding.key == key)


def _run_audit(handler, *, level="standard", model="gpt-4o-mini", provider=OPENAI_COMPATIBLE, **kwargs):
    auditor = ModelAuditor(transport=httpx.MockTransport(handler))

    async def run():
        return await auditor.audit(
            provider=provider,
            api_key="sk-test",
            model=model,
            timeout=5,
            url_mode="auto",
            base_url="https://api.example.com/v1",
            level=level,
            **kwargs,
        )

    return asyncio.run(run())


def _chat_response(text: str, *, model: str = "gpt-4o-mini", finish: str = "stop",
                   output_tokens: int = 5, tool_calls: list[str] | None = None):
    message: dict[str, object] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {"name": name, "arguments": '{"city":"Tokyo"}'},
            }
            for index, name in enumerate(tool_calls)
        ]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": output_tokens},
        },
    )


class _ChatGateway:
    """可配置行为的假 OpenAI 兼容网关。"""

    def __init__(
        self,
        *,
        reported_model: str = "gpt-4o-mini",
        self_report: str = "GPT-4o mini",
        honor_max_tokens: bool = True,
        honor_system: bool = True,
        honor_stop: bool = True,
        support_tools: bool = True,
        support_json: bool = True,
        support_image: bool = True,
        deterministic: bool = True,
    ) -> None:
        self.reported_model = reported_model
        self.self_report = self_report
        self.honor_max_tokens = honor_max_tokens
        self.honor_system = honor_system
        self.honor_stop = honor_stop
        self.support_tools = support_tools
        self.support_json = support_json
        self.support_image = support_image
        self.deterministic = deterministic
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body.get("messages", [])
        user = messages[-1].get("content") if messages else ""
        has_system = any(message.get("role") == "system" for message in messages)
        has_image = isinstance(user, list)

        if body.get("tools"):
            if not self.support_tools:
                return httpx.Response(
                    400, json={"error": {"message": "tools are not supported"}}
                )
            return _chat_response("", model=self.reported_model, finish="tool_calls",
                                  tool_calls=[PROBE_TOOL_NAME])

        if has_image:
            if not self.support_image:
                return httpx.Response(
                    400, json={"error": {"message": "image input is not supported"}}
                )
            return _chat_response("IMAGE", model=self.reported_model)

        if body.get("response_format"):
            text = '{"ok": true}' if self.support_json else "Sure! ok is true."
            return _chat_response(text, model=self.reported_model)

        text = str(user)
        if "model that is generating" in text:
            return _chat_response(self.self_report, model=self.reported_model)
        if "ALPHA BRAVO" in text:
            output = (
                f"ALPHA BRAVO {STOP_SEQUENCE}"
                if self.honor_stop
                else f"ALPHA BRAVO {STOP_SEQUENCE} {STOP_TAIL_MARKER}"
            )
            return _chat_response(output, model=self.reported_model)
        if "capital of France" in text:
            output = SYSTEM_DIRECTIVE_MARKER if (self.honor_system and has_system) else "Paris"
            return _chat_response(output, model=self.reported_model)
        if "1 to 60" in text or "1 to 300" in text:
            if self.honor_max_tokens:
                return _chat_response("1, 2, 3, 4", model=self.reported_model,
                                      finish="length", output_tokens=MAX_TOKENS_PROBE_LIMIT)
            return _chat_response(
                ", ".join(str(number) for number in range(1, 61)),
                model=self.reported_model, finish="stop", output_tokens=600,
            )
        if "random English sentence" in text:
            index = 0 if self.deterministic else len(self.requests)
            return _chat_response(f"The ocean is deep {index}.", model=self.reported_model)
        return _chat_response("ok", model=self.reported_model)


# ── 模型标识归一化 ──────────────────────────────────────────────────────


def test_model_family_covers_common_vendors() -> None:
    assert model_family("claude-sonnet-4-5-20250929") == "claude"
    assert model_family("gpt-4o-mini") == "gpt"
    assert model_family("gemini-2.5-flash") == "gemini"
    assert model_family("deepseek-chat") == "deepseek"
    assert model_family("glm-4.6") == "glm"
    assert model_family("qwen3-max") == "qwen"
    assert model_family("kimi-k2") == "kimi"
    assert model_family("some-unknown-model") == ""


def test_normalize_model_name_strips_version_noise() -> None:
    assert normalize_model_name("us.anthropic.claude-sonnet-4-20250514-v1:0") == "claude-sonnet-4"
    assert normalize_model_name("models/gemini-2.5-flash") == "gemini-2.5-flash"
    assert normalize_model_name("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet"
    assert normalize_model_name("gpt-4o-mini") == "gpt-4o-mini"


def test_models_match_tolerates_date_suffix() -> None:
    assert models_match("claude-sonnet-4-5", "claude-sonnet-4-5-20250929")
    assert models_match("gpt-4o-mini", "gpt-4o-mini")
    assert not models_match("claude-sonnet-4-5", "claude-haiku-3-5")
    assert not models_match("gpt-4o", "")


def test_self_reported_family_ignores_evasive_answers() -> None:
    assert self_reported_family("I am Claude, made by Anthropic.") == "claude"
    assert self_reported_family("我无法透露我的模型名称") == ""
    assert self_reported_family("") == ""


# ── 协议适配 ────────────────────────────────────────────────────────────


def test_protocol_of_maps_every_provider() -> None:
    assert protocol_of(OPENAI_COMPATIBLE) == PROTOCOL_OPENAI_CHAT
    assert protocol_of(OPENAI_RESPONSES) == PROTOCOL_OPENAI_RESPONSES
    assert protocol_of(ANTHROPIC) == PROTOCOL_ANTHROPIC
    assert protocol_of(GOOGLE_GEMINI) == PROTOCOL_GEMINI
    assert protocol_of(AMAZON_BEDROCK) == PROTOCOL_BEDROCK


def test_probe_body_uses_native_parameter_names() -> None:
    spec = ProbeSpec(user="hi", system="sys", max_tokens=8, temperature=0.0, stop=["X"])

    chat = build_probe_body(PROTOCOL_OPENAI_CHAT, "gpt-4o", spec)
    assert chat["max_tokens"] == 8
    assert chat["stop"] == ["X"]
    assert chat["messages"][0] == {"role": "system", "content": "sys"}

    anthropic = build_probe_body(PROTOCOL_ANTHROPIC, "claude-x", spec)
    assert anthropic["max_tokens"] == 8
    assert anthropic["stop_sequences"] == ["X"]
    assert anthropic["system"] == "sys"

    gemini = build_probe_body(PROTOCOL_GEMINI, "gemini-x", spec)
    assert gemini["generationConfig"]["maxOutputTokens"] == 8
    assert gemini["generationConfig"]["stopSequences"] == ["X"]
    assert gemini["systemInstruction"] == {"parts": [{"text": "sys"}]}

    bedrock = build_probe_body(PROTOCOL_BEDROCK, "nova", spec)
    assert bedrock["inferenceConfig"]["maxTokens"] == 8
    assert bedrock["inferenceConfig"]["stopSequences"] == ["X"]
    assert bedrock["system"] == [{"text": "sys"}]

    responses = build_probe_body(PROTOCOL_OPENAI_RESPONSES, "gpt-4o", spec)
    assert responses["max_output_tokens"] == 8
    assert responses["instructions"] == "sys"
    # Responses API 没有 stop 参数，不应被塞进请求体。
    assert "stop" not in responses


def test_probe_body_tool_definitions_are_protocol_native() -> None:
    spec = ProbeSpec(tools=True)
    assert build_probe_body(PROTOCOL_OPENAI_CHAT, "m", spec)["tools"][0]["function"]["name"] == PROBE_TOOL_NAME
    assert build_probe_body(PROTOCOL_ANTHROPIC, "m", spec)["tools"][0]["name"] == PROBE_TOOL_NAME
    assert (
        build_probe_body(PROTOCOL_GEMINI, "m", spec)["tools"][0]["functionDeclarations"][0]["name"]
        == PROBE_TOOL_NAME
    )
    assert (
        build_probe_body(PROTOCOL_BEDROCK, "m", spec)["toolConfig"]["tools"][0]["toolSpec"]["name"]
        == PROBE_TOOL_NAME
    )


def test_parse_probe_response_per_protocol() -> None:
    chat = parse_probe_response(
        PROTOCOL_OPENAI_CHAT,
        {
            "model": "gpt-4o",
            "choices": [
                {
                    "message": {
                        "content": "hi",
                        "tool_calls": [{"function": {"name": "t"}}],
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
    )
    assert (chat.reported_model, chat.text, chat.finish_reason) == ("gpt-4o", "hi", "length")
    assert chat.tool_calls == ["t"]
    assert (chat.input_tokens, chat.output_tokens) == (3, 4)

    anthropic = parse_probe_response(
        PROTOCOL_ANTHROPIC,
        {
            "model": "claude-x",
            "content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "name": "t"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    )
    assert anthropic.reported_model == "claude-x"
    assert anthropic.finish_reason == "max_tokens"
    assert anthropic.tool_calls == ["t"]

    gemini = parse_probe_response(
        PROTOCOL_GEMINI,
        {
            "modelVersion": "gemini-2.5-flash",
            "candidates": [
                {
                    "content": {"parts": [{"text": "hi"}, {"functionCall": {"name": "t"}}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4},
        },
    )
    assert gemini.reported_model == "gemini-2.5-flash"
    assert gemini.tool_calls == ["t"]
    assert gemini.output_tokens == 4

    bedrock = parse_probe_response(
        PROTOCOL_BEDROCK,
        {
            "output": {"message": {"content": [{"text": "hi"}, {"toolUse": {"name": "t"}}]}},
            "stopReason": "max_tokens",
            "usage": {"inputTokens": 3, "outputTokens": 4},
        },
    )
    assert bedrock.text == "hi"
    assert bedrock.tool_calls == ["t"]

    responses = parse_probe_response(
        PROTOCOL_OPENAI_RESPONSES,
        {
            "model": "gpt-4o",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "hi"}]},
                {"type": "function_call", "name": "t"},
            ],
            "usage": {"input_tokens": 3, "output_tokens": 4},
            "status": "completed",
        },
    )
    assert responses.text == "hi"
    assert responses.tool_calls == ["t"]


def test_supports_marks_unsupported_features() -> None:
    assert not supports(PROTOCOL_OPENAI_RESPONSES, "stop")
    assert supports(PROTOCOL_ANTHROPIC, "stop")
    assert not supports(PROTOCOL_ANTHROPIC, "json_mode")
    assert supports(PROTOCOL_OPENAI_CHAT, "json_mode")
    assert not supports(PROTOCOL_BEDROCK, "reported_model")


# ── 体检：模型身份 ──────────────────────────────────────────────────────


def test_audit_passes_for_healthy_gateway() -> None:
    report = _run_audit(_ChatGateway(), level="deep", known_models=["gpt-4o-mini", "gpt-4o"])

    assert report.error_message == ""
    assert report.summary_verdict == Verdict.PASS
    assert _finding(report, "reported_model").verdict == Verdict.PASS
    assert _finding(report, "self_report").verdict == Verdict.PASS
    assert _finding(report, "model_listed").verdict == Verdict.PASS
    assert _finding(report, "max_tokens").verdict == Verdict.PASS
    assert _finding(report, "system").verdict == Verdict.PASS
    assert _finding(report, "stop").verdict == Verdict.PASS
    assert _finding(report, "tools").verdict == Verdict.PASS
    assert _finding(report, "json_mode").verdict == Verdict.PASS
    assert _finding(report, "vision").verdict == Verdict.PASS
    assert _finding(report, "determinism").verdict == Verdict.PASS


def test_audit_flags_cross_family_model_substitution() -> None:
    gateway = _ChatGateway(reported_model="qwen3-max", self_report="I am Qwen, built by Alibaba.")
    report = _run_audit(gateway, model="claude-sonnet-4-5")

    reported = _finding(report, "reported_model")
    assert reported.verdict == Verdict.FAIL
    assert "qwen3-max" in reported.detail
    assert _finding(report, "self_report").verdict == Verdict.FAIL
    assert report.summary_verdict == Verdict.FAIL
    assert report.identity.configured_family == "claude"
    assert report.identity.reported_family == "qwen"


def test_audit_warns_on_same_family_downgrade() -> None:
    gateway = _ChatGateway(reported_model="claude-haiku-3-5", self_report="I am Claude.")
    report = _run_audit(gateway, model="claude-opus-4-1", level="quick")

    reported = _finding(report, "reported_model")
    assert reported.verdict == Verdict.WARN
    assert "降级" in reported.detail
    # 自述族系一致，不应误判为异常。
    assert _finding(report, "self_report").verdict == Verdict.PASS


def test_audit_warns_when_gateway_hides_model_identity() -> None:
    gateway = _ChatGateway(reported_model="", self_report="I am an assistant.")
    report = _run_audit(gateway, level="quick")

    assert _finding(report, "reported_model").verdict == Verdict.WARN
    assert _finding(report, "self_report").verdict == Verdict.UNKNOWN


def test_audit_warns_when_model_absent_from_model_list() -> None:
    report = _run_audit(_ChatGateway(), level="quick", known_models=["gpt-4o", "o3-mini"])
    assert _finding(report, "model_listed").verdict == Verdict.WARN


def test_audit_skips_model_list_check_without_model_list() -> None:
    report = _run_audit(_ChatGateway(), level="quick")
    assert _finding(report, "model_listed").verdict == Verdict.SKIP
    assert report.identity.listed_in_model_list is None


def test_audit_detects_unstable_routing_across_probes() -> None:
    models = iter(["gpt-4o-mini", "gpt-3.5-turbo", "gpt-4o-mini", "gpt-4o-mini", "gpt-4o-mini"])

    def handler(request: httpx.Request) -> httpx.Response:
        return _chat_response("GPT-4o mini", model=next(models, "gpt-4o-mini"))

    report = _run_audit(handler, level="quick")
    route = _finding(report, "route_stability")
    assert route.verdict == Verdict.FAIL
    assert "gpt-3.5-turbo" in route.detail


def test_audit_falls_back_to_bearer_when_anthropic_auth_fails() -> None:
    """Anthropic 中转网关常见场景：x-api-key 401，需切到 Authorization: Bearer。

    与 LLMChecker 的 Anthropic→OpenAI 兼容鉴权回退行为对齐。
    """

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "x-api-key" in request.headers:
            return httpx.Response(
                401, json={"error": {"message": "Authorization Not Found"}}
            )
        assert request.headers["authorization"] == "Bearer sk-test"
        # 请求体仍应是 Anthropic Messages 格式（保留协议，只换鉴权头）。
        body = json.loads(request.content)
        assert body["model"] == "claude-sonnet-4-5"
        system = body.get("system") or ""
        if isinstance(system, str) and "KEYPROBE-OK" in system:
            text = "KEYPROBE-OK"
        elif body.get("tools"):
            return httpx.Response(
                200,
                json={
                    "model": "claude-sonnet-4-5",
                    "content": [{"type": "tool_use", "name": PROBE_TOOL_NAME, "input": {}}],
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 4, "output_tokens": 4},
                },
            )
        else:
            text = "I am Claude."
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": text}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 4},
            },
        )

    report = _run_audit(
        handler, provider=ANTHROPIC, model="claude-sonnet-4-5", level="standard"
    )

    # 首次 x-api-key 失败，第二次起改用 Bearer，体检应正常完成。
    assert "x-api-key" in calls[0].headers
    assert calls[1].headers["authorization"] == "Bearer sk-test"
    assert report.error_message == ""
    assert report.summary_verdict == Verdict.PASS
    assert report.call_count == 6  # 1 次失败的 identity + 5 次标准档探针


def test_audit_still_aborts_when_bearer_fallback_also_fails() -> None:
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    report = _run_audit(handler, provider=ANTHROPIC, level="standard")
    # x-api-key 失败后切 Bearer 再失败，应当终止。
    assert calls[0] == 2
    assert "invalid api key" in report.error_message
    assert report.findings == []


# ── 体检：参数篡改 ──────────────────────────────────────────────────────


def test_audit_detects_ignored_max_tokens() -> None:
    report = _run_audit(_ChatGateway(honor_max_tokens=False), level="quick")

    finding = _finding(report, "max_tokens")
    assert finding.verdict == Verdict.FAIL
    assert finding.category == CATEGORY_PARAMS
    assert "上限明显未生效" in finding.detail


def test_audit_max_tokens_probe_is_short_to_avoid_blowing_tpm() -> None:
    """max_tokens 探针的长输出提示词已缩短到 1~60，避免被忽略时产出巨量 token 拖垮后续探针。"""

    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = str(body["messages"][-1]["content"])
        if "1 to 60" in user:
            captured.append(user)
            return _chat_response("1, 2, 3, 4", model="gpt-4o-mini", finish="length",
                                  output_tokens=8)
        return _ChatGateway()(request)

    _run_audit(handler, level="quick")
    assert captured, "long-output probe should use the shortened 1~60 prompt"
    assert "1 to 60" in captured[0]
    assert "1 to 300" not in captured[0]


def test_audit_rates_429_as_rate_limited_not_failure() -> None:
    """429 是限流，不是能力缺失。不应判 FAIL，应判 RATE_LIMITED。"""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # 第一个探针（identity）正常通过，后续探针一律 429。
        if "model that is generating" in str(body["messages"][-1]["content"]):
            return _chat_response("GPT-4o mini", model="gpt-4o-mini")
        return httpx.Response(429, json={"error": {"message": "inference tpm exhausted"}})

    report = _run_audit(handler, level="deep")

    tools = _finding(report, "tools")
    assert tools.verdict == Verdict.RATE_LIMITED
    assert tools.category == CATEGORY_CAPABILITY
    assert "429" in tools.detail
    assert not tools.is_problem

    for key in ("system", "stop", "max_tokens"):
        assert _finding(report, key).verdict == Verdict.RATE_LIMITED

    # 限流不应计入 problems。
    assert not any(finding.verdict == Verdict.FAIL for finding in report.findings)
    assert report.summary_verdict == Verdict.RATE_LIMITED


def test_audit_self_report_downgrades_when_identity_already_matches() -> None:
    """服务端回传模型已与配置一致时，模型自述不一致降为 WARN（疑似幻觉），不判 FAIL。"""

    gateway = _ChatGateway(reported_model="deepseek-v4-flash-0731", self_report="ChatGPT")
    report = _run_audit(gateway, model="deepseek-v4-flash", level="quick")

    reported = _finding(report, "reported_model")
    assert reported.verdict == Verdict.PASS  # 身份已通过
    self_report = _finding(report, "self_report")
    assert self_report.verdict == Verdict.WARN  # 降级，不再 FAIL
    assert self_report.confidence == "low"
    assert "幻觉" in self_report.detail


def test_audit_self_report_still_fails_when_identity_does_not_match() -> None:
    """身份本身就不一致时，自述不一致仍判 FAIL。"""

    gateway = _ChatGateway(reported_model="qwen3-max", self_report="I am Qwen.")
    report = _run_audit(gateway, model="claude-sonnet-4-5", level="quick")
    assert _finding(report, "self_report").verdict == Verdict.FAIL


def test_audit_detects_dropped_system_prompt() -> None:
    report = _run_audit(_ChatGateway(honor_system=False))

    finding = _finding(report, "system")
    assert finding.verdict == Verdict.WARN
    assert "system" in finding.title


def test_audit_detects_ignored_stop_sequence() -> None:
    report = _run_audit(_ChatGateway(honor_stop=False))
    assert _finding(report, "stop").verdict == Verdict.WARN


def test_audit_skips_stop_for_responses_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "stop" not in body
        output: list[dict] = []
        if body.get("tools"):
            output.append({"type": "function_call", "name": PROBE_TOOL_NAME})
        else:
            output.append(
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "GPT-4o mini"}],
                }
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "output": output,
                "usage": {"input_tokens": 4, "output_tokens": 4},
                "status": "completed",
            },
        )

    report = _run_audit(handler, provider=OPENAI_RESPONSES)
    assert _finding(report, "stop").verdict == Verdict.SKIP


def test_audit_flags_nondeterministic_output_at_temperature_zero() -> None:
    report = _run_audit(_ChatGateway(deterministic=False), level="deep")
    finding = _finding(report, "determinism")
    assert finding.verdict == Verdict.WARN
    assert finding.confidence == "low"


def test_audit_probe_request_bodies_carry_expected_parameters() -> None:
    gateway = _ChatGateway()
    report = _run_audit(gateway, level="deep")

    max_tokens_probe = next(probe for probe in report.probes if probe.key == "max_tokens")
    assert max_tokens_probe.request_body["max_tokens"] == MAX_TOKENS_PROBE_LIMIT
    assert max_tokens_probe.request_body["temperature"] == 0.0

    system_probe = next(probe for probe in report.probes if probe.key == "system")
    assert system_probe.request_body["messages"][0]["role"] == "system"


# ── 体检：能力 ──────────────────────────────────────────────────────────


def test_audit_reports_missing_tool_capability() -> None:
    report = _run_audit(_ChatGateway(support_tools=False))
    finding = _finding(report, "tools")
    assert finding.verdict == Verdict.FAIL
    assert finding.category == CATEGORY_CAPABILITY


def test_audit_warns_when_tool_call_is_stripped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("tools"):
            # 网关声称支持工具，但把工具调用换成了普通文本。
            return _chat_response("You are in Tokyo.")
        return _ChatGateway()(request)

    report = _run_audit(handler)
    assert _finding(report, "tools").verdict == Verdict.WARN


def test_audit_flags_json_mode_and_vision_gaps() -> None:
    report = _run_audit(
        _ChatGateway(support_json=False, support_image=False), level="deep"
    )
    assert _finding(report, "json_mode").verdict == Verdict.WARN
    assert _finding(report, "vision").verdict == Verdict.FAIL


def test_audit_accepts_json_wrapped_in_code_fence() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("response_format"):
            return _chat_response('```json\n{"ok": true}\n```')
        return _ChatGateway()(request)

    report = _run_audit(handler, level="deep")
    assert _finding(report, "json_mode").verdict == Verdict.PASS


def test_audit_skips_json_mode_for_anthropic_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "response_format" not in body
        content: list[dict] = []
        if body.get("tools"):
            content.append({"type": "tool_use", "name": PROBE_TOOL_NAME, "input": {}})
        else:
            content.append({"type": "text", "text": "I am Claude."})
        return httpx.Response(
            200,
            json={
                "model": "claude-sonnet-4-5-20250929",
                "content": content,
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 4},
            },
        )

    report = _run_audit(handler, provider=ANTHROPIC, model="claude-sonnet-4-5", level="deep")
    assert _finding(report, "json_mode").verdict == Verdict.SKIP
    assert _finding(report, "reported_model").verdict == Verdict.PASS


# ── 体检：失败与边界 ────────────────────────────────────────────────────


def test_audit_stops_when_first_probe_is_unauthorized() -> None:
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    report = _run_audit(handler, level="deep")
    assert calls[0] == 1
    assert "invalid api key" in report.error_message
    assert report.findings == []
    assert report.summary_verdict == Verdict.UNKNOWN


def test_audit_requires_api_key_and_model() -> None:
    auditor = ModelAuditor()

    async def run(api_key: str, model: str):
        return await auditor.audit(
            provider=OPENAI_COMPATIBLE, api_key=api_key, model=model, timeout=5,
            url_mode="auto", base_url="https://api.example.com/v1",
        )

    assert asyncio.run(run("", "gpt-4o")).error_message == "API Key 不能为空"
    assert asyncio.run(run("sk-test", "")).error_message == "请先选择或填写模型"


def test_audit_level_controls_call_count() -> None:
    quick = _run_audit(_ChatGateway(), level="quick")
    standard = _run_audit(_ChatGateway(), level="standard")
    deep = _run_audit(_ChatGateway(), level="deep")

    assert quick.call_count == 2
    assert standard.call_count == 5
    assert deep.call_count == 9
    assert quick.call_count < standard.call_count < deep.call_count


def test_audit_handles_network_error_on_later_probe() -> None:
    calls = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        if calls[0] == 1:
            return _chat_response("GPT-4o mini")
        raise httpx.ConnectError("connection reset", request=request)

    report = _run_audit(handler, level="quick")
    finding = _finding(report, "max_tokens")
    assert finding.verdict == Verdict.UNKNOWN
    assert finding.confidence == "low"


def test_audit_estimates_tokens_when_usage_is_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        text = "GPT-4o mini"
        if "1 to 60" in str(user) or "1 to 300" in str(user):
            text = ", ".join(str(number) for number in range(1, 61))
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o-mini",
                "choices": [
                    {"message": {"content": text}, "finish_reason": "stop"}
                ],
            },
        )

    report = _run_audit(handler, level="quick")
    finding = _finding(report, "max_tokens")
    assert finding.verdict == Verdict.FAIL
    assert finding.confidence == "medium"
    assert "按文本长度估算" in finding.detail


def test_audit_report_text_contains_all_sections() -> None:
    report = _run_audit(_ChatGateway(), level="deep", known_models=["gpt-4o-mini"])
    text = report.to_text()

    assert "KeyProbe 模型体检报告" in text
    assert CATEGORY_IDENTITY in text
    assert CATEGORY_PARAMS in text
    assert CATEGORY_CAPABILITY in text
    assert "gpt-4o-mini" in text
    assert "探针留痕" in text


def test_audit_never_records_api_key_in_report() -> None:
    report = _run_audit(_ChatGateway(), level="deep")
    serialized = report.model_dump_json()
    assert "sk-test" not in serialized
