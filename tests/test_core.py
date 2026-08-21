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
    full_url_model_urls,
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
    assert openai_model_urls("https://api.gemai.cc/v1") == [
        "https://api.gemai.cc/v1/models",
    ]
    assert openai_test_urls("https://api.gemai.cc/v1") == [
        "https://api.gemai.cc/v1/chat/completions",
    ]


def test_openai_url_builder_appends_suffix_only() -> None:
    assert openai_model_urls("https://api.openai.com") == [
        "https://api.openai.com/v1/models",
        "https://api.openai.com/models",
    ]
    assert openai_test_urls("https://api.openai.com") == [
        "https://api.openai.com/chat/completions"
    ]


def test_openai_url_builder_prefers_models_for_versioned_base() -> None:
    assert openai_model_urls("https://open.bigmodel.cn/api/coding/paas/v4") == [
        "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "https://open.bigmodel.cn/api/coding/paas/v4/v1/models",
    ]
    assert openai_model_urls("https://api.example.com/v10") == [
        "https://api.example.com/v10/models",
        "https://api.example.com/v10/v1/models",
    ]


def test_full_request_url_derives_models_endpoint() -> None:
    assert full_url_model_urls("https://token.sensenova.cn/v1/messages") == [
        "https://token.sensenova.cn/v1/models",
    ]
    assert full_url_model_urls("https://proxy.example.com/v1/chat/completions") == [
        "https://proxy.example.com/v1/models",
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
    assert anthropic_model_urls("https://open.bigmodel.cn/api/anthropic") == [
        "https://open.bigmodel.cn/api/anthropic/v1/models",
        "https://open.bigmodel.cn/api/anthropic/models",
        "https://open.bigmodel.cn/v1/models",
        "https://open.bigmodel.cn/models",
    ]
    assert anthropic_model_urls("https://dashscope.aliyuncs.com/apps/anthropic") == [
        "https://dashscope.aliyuncs.com/apps/anthropic/v1/models",
        "https://dashscope.aliyuncs.com/apps/anthropic/models",
        "https://dashscope.aliyuncs.com/v1/models",
        "https://dashscope.aliyuncs.com/models",
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


def test_fetch_models_full_url_derives_model_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "claude-sonnet-test"}]})

    async def run_case() -> None:
        result = await LLMChecker(transport=httpx.MockTransport(handler)).fetch_models(
            provider=ANTHROPIC,
            api_key="sk-test",
            timeout=5,
            url_mode="full",
            base_url="https://token.sensenova.cn/v1/messages",
        )
        assert result.models == ["claude-sonnet-test"]

    asyncio.run(run_case())

    assert str(requests[0].url) == "https://token.sensenova.cn/v1/models"


def test_anthropic_auth_fallback_keeps_messages_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            assert request.headers["x-api-key"] == "sk-test"
            return httpx.Response(401, json={"error": {"message": "Authorization Not Found"}})

        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer sk-test"
        assert body == {
            "model": "claude-test",
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hello"}],
        }
        return httpx.Response(200, json={"id": "ok"})

    async def run_case() -> None:
        result = await LLMChecker(transport=httpx.MockTransport(handler)).test_model(
            provider=ANTHROPIC,
            api_key="sk-test",
            model="claude-test",
            timeout=5,
            url_mode="full",
            base_url="https://token.sensenova.cn/v1/messages",
        )
        assert result.status.value == "VALID"

    asyncio.run(run_case())

    assert [str(request.url) for request in requests] == [
        "https://token.sensenova.cn/v1/messages",
        "https://token.sensenova.cn/v1/messages",
    ]


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
            ANTHROPIC, "https://api.anthropic.com", "claude-sonnet-test", "anthropic-key",
            "https://api.anthropic.com/v1/messages",
            {"model": "claude-sonnet-test", "max_tokens": 16, "messages": [{"role": "user", "content": "hello"}]},
            "x-api-key",
        ),
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


def test_asyncio_run_reuse_same_checker() -> None:
    """Regression: repeated asyncio.run() on the same checker must not raise
    "Event loop is closed" (previously caused by sharing a single httpx.AsyncClient
    across event loops)."""
    request_count: list[int] = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        request_count[0] += 1
        return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})

    checker = LLMChecker(transport=httpx.MockTransport(handler))

    async def run():
        return await checker.fetch_models(
            provider=OPENAI_COMPATIBLE, api_key="sk-test", timeout=5,
            url_mode="auto", base_url="https://test.example.com/v1",
        )

    for _ in range(3):
        result = asyncio.run(run())
        assert result.models == ["gpt-test"]

    assert request_count[0] == 3
