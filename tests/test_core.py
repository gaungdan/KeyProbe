import httpx

from app.checker.client import LLMChecker
from app.services.providers import ANTHROPIC, AMAZON_BEDROCK, GOOGLE_GEMINI, OPENAI_COMPATIBLE, detect_provider
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
