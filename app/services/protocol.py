"""协议适配层：为体检探针构造请求体并解析响应。

KeyProbe 支持 5 种原生协议，它们的请求参数名与响应结构互不相同。体检需要
按同一套语义（max_tokens / temperature / stop / system / tools / json / image）
下发参数，再从响应里取出同一套语义的结果（回传模型、文本、结束原因、token 用量、
工具调用），因此在这里做一次统一映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.providers import (
    AMAZON_BEDROCK,
    GOOGLE_GEMINI,
    OPENAI_RESPONSES,
    is_anthropic_provider,
)

# 协议标识
PROTOCOL_OPENAI_CHAT = "openai_chat"
PROTOCOL_OPENAI_RESPONSES = "openai_responses"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_GEMINI = "gemini"
PROTOCOL_BEDROCK = "bedrock"

PROTOCOL_LABELS = {
    PROTOCOL_OPENAI_CHAT: "OpenAI Chat Completions",
    PROTOCOL_OPENAI_RESPONSES: "OpenAI Responses",
    PROTOCOL_ANTHROPIC: "Anthropic Messages",
    PROTOCOL_GEMINI: "Gemini generateContent",
    PROTOCOL_BEDROCK: "Bedrock Converse",
}

# 1x1 像素透明 PNG，用于视觉能力探针（体积最小，几乎不消耗图像 token）。
TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)

PROBE_TOOL_NAME = "keyprobe_report_city"
PROBE_TOOL_DESCRIPTION = "Report the city mentioned by the user."
PROBE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"city": {"type": "string", "description": "The city name."}},
    "required": ["city"],
}


def protocol_of(provider: str) -> str:
    """把 UI 上的服务商/协议名映射到协议标识。"""

    if is_anthropic_provider(provider):
        return PROTOCOL_ANTHROPIC
    if provider == GOOGLE_GEMINI:
        return PROTOCOL_GEMINI
    if provider == AMAZON_BEDROCK:
        return PROTOCOL_BEDROCK
    if provider == OPENAI_RESPONSES:
        return PROTOCOL_OPENAI_RESPONSES
    return PROTOCOL_OPENAI_CHAT


@dataclass(frozen=True)
class ProbeSpec:
    """一次探针调用的语义化参数。"""

    user: str = "hello"
    system: str = ""
    max_tokens: int = 16
    temperature: float | None = 0.0
    stop: list[str] = field(default_factory=list)
    json_mode: bool = False
    tools: bool = False
    image: bool = False


@dataclass(frozen=True)
class ParsedProbe:
    """从响应体解析出的统一结果。"""

    reported_model: str = ""
    text: str = ""
    finish_reason: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list[str] = field(default_factory=list)
    has_usage: bool = False


def supports(protocol: str, feature: str) -> bool:
    """判断协议是否支持某个体检特性。

    不支持时体检会把该项标记为「跳过」，而不是判为异常。
    """

    if feature == "stop":
        # Responses API 没有 stop 参数。
        return protocol != PROTOCOL_OPENAI_RESPONSES
    if feature == "json_mode":
        return protocol in {
            PROTOCOL_OPENAI_CHAT,
            PROTOCOL_OPENAI_RESPONSES,
            PROTOCOL_GEMINI,
        }
    if feature == "tools":
        return True
    if feature == "image":
        return True
    if feature == "system":
        return True
    if feature == "reported_model":
        # Bedrock InvokeModel 响应不回传模型标识。
        return protocol != PROTOCOL_BEDROCK
    return False


def build_probe_body(protocol: str, model: str, spec: ProbeSpec) -> dict[str, Any]:
    """按协议构造探针请求体。"""

    builders = {
        PROTOCOL_OPENAI_CHAT: _openai_chat_body,
        PROTOCOL_OPENAI_RESPONSES: _openai_responses_body,
        PROTOCOL_ANTHROPIC: _anthropic_body,
        PROTOCOL_GEMINI: _gemini_body,
        PROTOCOL_BEDROCK: _bedrock_body,
    }
    return builders.get(protocol, _openai_chat_body)(model, spec)


def parse_probe_response(protocol: str, payload: Any) -> ParsedProbe:
    """按协议解析响应体。"""

    if not isinstance(payload, dict):
        return ParsedProbe()
    parsers = {
        PROTOCOL_OPENAI_CHAT: _parse_openai_chat,
        PROTOCOL_OPENAI_RESPONSES: _parse_openai_responses,
        PROTOCOL_ANTHROPIC: _parse_anthropic,
        PROTOCOL_GEMINI: _parse_gemini,
        PROTOCOL_BEDROCK: _parse_bedrock,
    }
    return parsers.get(protocol, _parse_openai_chat)(payload)


# ── 请求体构造 ──────────────────────────────────────────────────────────


def _openai_chat_body(model: str, spec: ProbeSpec) -> dict[str, Any]:
    content: Any = spec.user
    if spec.image:
        content = [
            {"type": "text", "text": spec.user},
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{TINY_PNG_BASE64}"},
            },
        ]
    messages: list[dict[str, Any]] = []
    if spec.system:
        messages.append({"role": "system", "content": spec.system})
    messages.append({"role": "user", "content": content})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": spec.max_tokens,
    }
    if spec.temperature is not None:
        body["temperature"] = spec.temperature
    if spec.stop:
        body["stop"] = list(spec.stop)
    if spec.json_mode:
        body["response_format"] = {"type": "json_object"}
    if spec.tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": PROBE_TOOL_NAME,
                    "description": PROBE_TOOL_DESCRIPTION,
                    "parameters": PROBE_TOOL_SCHEMA,
                },
            }
        ]
        body["tool_choice"] = "required"
    return body


def _openai_responses_body(model: str, spec: ProbeSpec) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": spec.user}]
    if spec.image:
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{TINY_PNG_BASE64}",
            }
        )
    body: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": spec.max_tokens,
    }
    if spec.system:
        body["instructions"] = spec.system
    if spec.temperature is not None:
        body["temperature"] = spec.temperature
    if spec.json_mode:
        body["text"] = {"format": {"type": "json_object"}}
    if spec.tools:
        body["tools"] = [
            {
                "type": "function",
                "name": PROBE_TOOL_NAME,
                "description": PROBE_TOOL_DESCRIPTION,
                "parameters": PROBE_TOOL_SCHEMA,
            }
        ]
        body["tool_choice"] = "required"
    return body


def _anthropic_body(model: str, spec: ProbeSpec) -> dict[str, Any]:
    content: Any = spec.user
    if spec.image:
        content = [
            {"type": "text", "text": spec.user},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": TINY_PNG_BASE64,
                },
            },
        ]
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": spec.max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if spec.system:
        body["system"] = spec.system
    if spec.temperature is not None:
        body["temperature"] = spec.temperature
    if spec.stop:
        body["stop_sequences"] = list(spec.stop)
    if spec.tools:
        body["tools"] = [
            {
                "name": PROBE_TOOL_NAME,
                "description": PROBE_TOOL_DESCRIPTION,
                "input_schema": PROBE_TOOL_SCHEMA,
            }
        ]
        body["tool_choice"] = {"type": "any"}
    return body


def _gemini_body(model: str, spec: ProbeSpec) -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": spec.user}]
    if spec.image:
        parts.append({"inlineData": {"mimeType": "image/png", "data": TINY_PNG_BASE64}})

    generation_config: dict[str, Any] = {"maxOutputTokens": spec.max_tokens}
    if spec.temperature is not None:
        generation_config["temperature"] = spec.temperature
    if spec.stop:
        generation_config["stopSequences"] = list(spec.stop)
    if spec.json_mode:
        generation_config["responseMimeType"] = "application/json"

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": generation_config,
    }
    if spec.system:
        body["systemInstruction"] = {"parts": [{"text": spec.system}]}
    if spec.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": PROBE_TOOL_NAME,
                        "description": PROBE_TOOL_DESCRIPTION,
                        "parameters": PROBE_TOOL_SCHEMA,
                    }
                ]
            }
        ]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
    return body


def _bedrock_body(model: str, spec: ProbeSpec) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"text": spec.user}]
    if spec.image:
        content.append(
            {"image": {"format": "png", "source": {"bytes": TINY_PNG_BASE64}}}
        )

    inference_config: dict[str, Any] = {"maxTokens": spec.max_tokens}
    if spec.temperature is not None:
        inference_config["temperature"] = spec.temperature
    if spec.stop:
        inference_config["stopSequences"] = list(spec.stop)

    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": inference_config,
    }
    if spec.system:
        body["system"] = [{"text": spec.system}]
    if spec.tools:
        body["toolConfig"] = {
            "tools": [
                {
                    "toolSpec": {
                        "name": PROBE_TOOL_NAME,
                        "description": PROBE_TOOL_DESCRIPTION,
                        "inputSchema": {"json": PROBE_TOOL_SCHEMA},
                    }
                }
            ],
            "toolChoice": {"any": {}},
        }
    return body


# ── 响应解析 ────────────────────────────────────────────────────────────


def _parse_openai_chat(payload: dict[str, Any]) -> ParsedProbe:
    choices = payload.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}

    texts: list[str] = []
    raw_content = message.get("content")
    if isinstance(raw_content, str):
        texts.append(raw_content)
    elif isinstance(raw_content, list):
        texts.extend(
            str(part.get("text", ""))
            for part in raw_content
            if isinstance(part, dict) and part.get("text")
        )
    # 部分兼容网关把内容放在 choices[0].text（Completions 风格）。
    if not texts and isinstance(choice.get("text"), str):
        texts.append(choice["text"])

    tool_calls: list[str] = []
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function")
            name = function.get("name") if isinstance(function, dict) else call.get("name")
            if name:
                tool_calls.append(str(name))
    legacy_call = message.get("function_call")
    if isinstance(legacy_call, dict) and legacy_call.get("name"):
        tool_calls.append(str(legacy_call["name"]))

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return ParsedProbe(
        reported_model=str(payload.get("model") or ""),
        text="".join(texts).strip(),
        finish_reason=str(choice.get("finish_reason") or choice.get("stop_reason") or ""),
        input_tokens=_int_or_none(usage.get("prompt_tokens") or usage.get("input_tokens")),
        output_tokens=_int_or_none(
            usage.get("completion_tokens") or usage.get("output_tokens")
        ),
        tool_calls=tool_calls,
        has_usage=bool(usage),
    )


def _parse_openai_responses(payload: dict[str, Any]) -> ParsedProbe:
    texts: list[str] = []
    tool_calls: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "function_call" and item.get("name"):
            tool_calls.append(str(item["name"]))
        for part in item.get("content") or []:
            if isinstance(part, dict) and part.get("text"):
                texts.append(str(part["text"]))
    if not texts and isinstance(payload.get("output_text"), str):
        texts.append(payload["output_text"])

    incomplete = payload.get("incomplete_details")
    finish_reason = ""
    if isinstance(incomplete, dict) and incomplete.get("reason"):
        finish_reason = str(incomplete["reason"])
    elif payload.get("status"):
        finish_reason = str(payload["status"])

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return ParsedProbe(
        reported_model=str(payload.get("model") or ""),
        text="".join(texts).strip(),
        finish_reason=finish_reason,
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        tool_calls=tool_calls,
        has_usage=bool(usage),
    )


def _parse_anthropic(payload: dict[str, Any]) -> ParsedProbe:
    texts: list[str] = []
    tool_calls: list[str] = []
    content = payload.get("content")
    if isinstance(content, str):
        texts.append(content)
    else:
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]))
            elif block.get("type") == "tool_use" and block.get("name"):
                tool_calls.append(str(block["name"]))

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return ParsedProbe(
        reported_model=str(payload.get("model") or ""),
        text="".join(texts).strip(),
        finish_reason=str(payload.get("stop_reason") or ""),
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
        tool_calls=tool_calls,
        has_usage=bool(usage),
    )


def _parse_gemini(payload: dict[str, Any]) -> ParsedProbe:
    candidates = payload.get("candidates")
    candidate = candidates[0] if isinstance(candidates, list) and candidates else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    content = candidate.get("content") if isinstance(candidate.get("content"), dict) else {}

    texts: list[str] = []
    tool_calls: list[str] = []
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            texts.append(str(part["text"]))
        call = part.get("functionCall")
        if isinstance(call, dict) and call.get("name"):
            tool_calls.append(str(call["name"]))

    usage = payload.get("usageMetadata") if isinstance(payload.get("usageMetadata"), dict) else {}
    return ParsedProbe(
        reported_model=str(payload.get("modelVersion") or ""),
        text="".join(texts).strip(),
        finish_reason=str(candidate.get("finishReason") or ""),
        input_tokens=_int_or_none(usage.get("promptTokenCount")),
        output_tokens=_int_or_none(usage.get("candidatesTokenCount")),
        tool_calls=tool_calls,
        has_usage=bool(usage),
    )


def _parse_bedrock(payload: dict[str, Any]) -> ParsedProbe:
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    message = output.get("message") if isinstance(output.get("message"), dict) else {}

    texts: list[str] = []
    tool_calls: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("text"):
            texts.append(str(block["text"]))
        use = block.get("toolUse")
        if isinstance(use, dict) and use.get("name"):
            tool_calls.append(str(use["name"]))

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return ParsedProbe(
        reported_model="",
        text="".join(texts).strip(),
        finish_reason=str(payload.get("stopReason") or ""),
        input_tokens=_int_or_none(usage.get("inputTokens")),
        output_tokens=_int_or_none(usage.get("outputTokens")),
        tool_calls=tool_calls,
        has_usage=bool(usage),
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None
