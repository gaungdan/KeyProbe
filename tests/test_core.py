import asyncio
import json

import httpx

from app.checker.client import LLMChecker
from app.services.bedrock_auth import parse_aws_credentials, sign_bedrock_request
from app.services.providers import (
    ANTHROPIC,
    AMAZON_BEDROCK,
    GOOGLE_GEMINI,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
    detect_provider,
)
from app.services.url_builder import (
    anthropic_model_urls,
    openai_model_urls,
    openai_test_urls,
)
from app.utils.security import mask_api_key


def test_openai_url_builder_avoids_duplicate_v1() -> None:
    assert openai_model_urls("https://api.openai.com/v1") == [
        "https://api.openai.com/v1/models",
    ]
    assert openai_test_urls("https://api.openai.com/v1") == [
        "https://api.openai.com/v1/chat/completions"
    ]


def test_openai_url_builder_appends_suffix_only() -> None:
    assert openai_model_urls("https://api.openai.com") == [
        "https://api.openai.com/v1/models",
        "https://api.openai.com/models",
    ]
    assert openai_test_urls("https://api.openai.com") == [
        "https://api.openai.com/chat/completions"
    ]


def test_anthropic_url_builder_tries_models_fallback() -> None:
    assert anthropic_model_urls("https://api.anthropic.com") == [
        "https://api.anthropic.com/v1/models",
        "https://api.anthropic.com/models",
    ]
    assert anthropic_model_urls("https://api.anthropic.com/v1") == [
        "https://api.anthropic.com/v1/models",
    ]
    assert anthropic_model_urls("https://token-plan-cn.xiaomimimo.com/anthropic") == [
        "https://token-plan-cn.xiaomimimo.com/anthropic/v1/models",
        "https://token-plan-cn.xiaomimimo.com/anthropic/models",
        "https://token-plan-cn.xiaomimimo.com/v1/models",
        "https://token-plan-cn.xiaomimimo.com/models",
    ]


def test_provider_detection() -> None:
    assert detect_provider("https://api.openai.com/v1") == OPENAI_COMPATIBLE
    assert detect_provider("https://openrouter.ai/api/v1") == OPENAI_COMPATIBLE
    assert detect_provider("https://api.deepseek.com") == OPENAI_COMPATIBLE
    assert detect_provider("https://api.anthropic.com") == ANTHROPIC
    assert detect_provider("https://token-plan-cn.xiaomimimo.com/anthropic") == ANTHROPIC
    assert detect_provider("https://example.com/claude/v1") == ANTHROPIC
    assert detect_provider("https://bedrock-runtime.us-east-1.amazonaws.com") == AMAZON_BEDROCK
    assert detect_provider("https://generativelanguage.googleapis.com") == GOOGLE_GEMINI
    assert detect_provider("https://example.com") == OPENAI_COMPATIBLE


def test_mask_api_key_never_returns_full_key() -> None:
    key = "sk-abcdefghijklmnopqrstuvwxyz"
    masked = mask_api_key(key)
    assert masked != key
    assert masked.startswith("sk-abc")
    assert masked.endswith("wxyz")
    assert "********" in masked


def test_parse_models_accepts_common_compatible_fields() -> None:
    response = httpx.Response(
        200,
        json={
            "data": [
                {"id": "claude-3-5-sonnet-20241022"},
                {"name": "claude-compatible-name"},
                {"model": "claude-compatible-model"},
            ],
            "models": ["claude-string-model"],
        },
    )

    assert LLMChecker()._parse_models(response) == [
        "claude-3-5-sonnet-20241022",
        "claude-compatible-model",
        "claude-compatible-name",
        "claude-string-model",
    ]


def test_parse_models_understands_bedrock_and_gemini_payloads() -> None:
    bedrock = httpx.Response(200, json={"modelSummaries": [{"modelId": "amazon.nova-lite-v1:0"}]})
    gemini = httpx.Response(200, json={"models": [{"name": "models/gemini-2.5-flash"}]})

    assert LLMChecker()._parse_models(bedrock, AMAZON_BEDROCK) == ["amazon.nova-lite-v1:0"]
    assert LLMChecker()._parse_models(gemini, GOOGLE_GEMINI) == ["gemini-2.5-flash"]


def test_anthropic_route_overrides_openai_provider_selection() -> None:
    checker = LLMChecker()

    assert checker._effective_provider(
        "OpenAl Compatible",
        "https://token-plan-cn.xiaomimimo.com/anthropic",
    ) == ANTHROPIC
    assert checker._model_urls(
        checker._effective_provider(
            "OpenAl Compatible",
            "https://token-plan-cn.xiaomimimo.com/anthropic",
        ),
        "https://token-plan-cn.xiaomimimo.com/anthropic",
    ) == [
        "https://token-plan-cn.xiaomimimo.com/anthropic/v1/models",
        "https://token-plan-cn.xiaomimimo.com/anthropic/models",
        "https://token-plan-cn.xiaomimimo.com/v1/models",
        "https://token-plan-cn.xiaomimimo.com/models",
    ]


def test_native_protocol_requests() -> None:
    cases = [
        (
            OPENAI_RESPONSES, "https://api.openai.com/v1", "gpt-4.1-mini", "sk-test",
            "https://api.openai.com/v1/responses",
            {"model": "gpt-4.1-mini", "input": "hello", "max_output_tokens": 16, "temperature": 0},
            "authorization",
        ),
        (
            GOOGLE_GEMINI, "https://generativelanguage.googleapis.com", "gemini-2.5-flash", "gem-key",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            {"contents": [{"role": "user", "parts": [{"text": "hello"}]}], "generationConfig": {"temperature": 0, "maxOutputTokens": 16}},
            "x-goog-api-key",
        ),
        (
            AMAZON_BEDROCK, "https://bedrock-runtime.us-east-1.amazonaws.com", "amazon.nova-lite-v1:0", "bedrock-key",
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/amazon.nova-lite-v1%3A0/invoke",
            {"messages": [{"role": "user", "content": [{"text": "hello"}]}], "inferenceConfig": {"maxTokens": 16, "temperature": 0}},
            "authorization",
        ),
    ]

    async def run_case(
        provider: str, base_url: str, model: str, api_key: str, expected_url: str,
        expected_body: dict[str, object], header_name: str,
    ) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"id": "ok"})

        result = await LLMChecker(transport=httpx.MockTransport(handler)).test_model(
            provider=provider, api_key=api_key, model=model, timeout=5,
            url_mode="auto", base_url=base_url,
        )
        assert result.status.value == "VALID"
        assert str(requests[0].url) == expected_url
        assert requests[0].headers[header_name]
        assert json.loads(requests[0].content) == expected_body

    async def run_all() -> None:
        for case in cases:
            await run_case(*case)

    asyncio.run(run_all())


def test_bedrock_sigv4_uses_runtime_service_and_session_token() -> None:
    credentials = parse_aws_credentials("AKID|secret|session")
    assert credentials == ("AKID", "secret", "session")
    headers = sign_bedrock_request(
        credentials=credentials,
        method="POST",
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/m/invoke",
        payload=b"{}",
    )
    assert "bedrock-runtime" in headers["Authorization"]
    assert headers["X-Amz-Security-Token"] == "session"
