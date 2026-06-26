"""Provider detection and provider-specific helpers."""

from urllib.parse import urlparse


OPENAI_RESPONSES = "OpenAI 响应接口"
OPENAI_COMPATIBLE = "OpenAI 兼容接口"
ANTHROPIC = "Anthropic Claude 协议"
AMAZON_BEDROCK = "亚马逊 Bedrock"
GOOGLE_GEMINI = "谷歌 Gemini"

PROVIDER_OPTIONS = [
    OPENAI_RESPONSES,
    OPENAI_COMPATIBLE,
    ANTHROPIC,
    AMAZON_BEDROCK,
    GOOGLE_GEMINI,
]

LEGACY_PROVIDER_NAMES = {
    "OpenAl Responses": OPENAI_RESPONSES,
    "OpenAI Responses": OPENAI_RESPONSES,
    "OpenAl Compatible": OPENAI_COMPATIBLE,
    "OpenAI Compatible": OPENAI_COMPATIBLE,
    "Anthropic": ANTHROPIC,
    "Amazon Bedrock": AMAZON_BEDROCK,
    "Google (Gemini)": GOOGLE_GEMINI,
}

ANTHROPIC_DEFAULT_MODELS = [
    "claude-sonnet-4",
    "claude-opus-4",
    "claude-haiku-3.5",
]


def detect_provider(base_url: str) -> str:
    """Infer the provider from a base URL."""

    raw_url = base_url.strip()
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if not host:
        host = raw_url.lower()
        path = raw_url.lower()

    if "api.openai.com" in host:
        return OPENAI_COMPATIBLE
    if "anthropic.com" in host or "/anthropic" in path or "/claude" in path:
        return ANTHROPIC
    if "bedrock" in host or "amazonaws.com" in host:
        return AMAZON_BEDROCK
    if "googleapis.com" in host or "generativelanguage" in host or "gemini" in host:
        return GOOGLE_GEMINI
    return OPENAI_COMPATIBLE


def resolve_provider(selected_provider: str, base_url: str) -> str:
    """Use the selected provider or auto-detect it when requested."""

    return normalize_provider(selected_provider) or detect_provider(base_url)


def normalize_provider(provider: str) -> str:
    """Map old saved provider labels to the current Chinese UI labels."""

    return LEGACY_PROVIDER_NAMES.get(provider, provider)


def is_anthropic_provider(provider: str) -> bool:
    """Return whether a provider should use Anthropic-style endpoints."""

    normalized = provider.lower()
    return "anthropic" in normalized or "claude" in normalized or "协议" in provider
