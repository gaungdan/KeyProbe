"""Build request URLs for auto and full URL modes."""

from collections.abc import Iterable
from urllib.parse import quote, urlparse, urlunparse


def normalize_base_url(base_url: str) -> str:
    """Normalize user input while preserving the path they provided."""

    return base_url.strip().rstrip("/")


def join_url(base_url: str, path: str) -> str:
    """Join a base URL and path, avoiding duplicate slashes and /v1/v1."""

    base = normalize_base_url(base_url)
    suffix = "/" + path.strip("/")
    if base.lower().endswith("/v1") and suffix.lower().startswith("/v1/"):
        suffix = suffix[3:]
    return f"{base}{suffix}"


def unique_urls(urls: Iterable[str]) -> list[str]:
    """Keep URL order while removing duplicates."""

    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def strip_known_protocol_suffix(base_url: str) -> str:
    """Remove route suffixes that may only apply to chat/message endpoints."""

    base = normalize_base_url(base_url)
    lowered = base.lower()
    for suffix in ("/anthropic/v1", "/anthropic", "/claude/v1", "/claude"):
        if lowered.endswith(suffix):
            return base[: -len(suffix)]
    return base


def openai_model_urls(base_url: str) -> list[str]:
    """Candidate model list URLs for OpenAI-compatible providers.

    Third-party proxies may expose the endpoint at /v1/models or /models.
    """

    return unique_urls([
        join_url(base_url, "/v1/models"),
        join_url(base_url, "/models"),
    ])


def openai_test_urls(base_url: str) -> list[str]:
    """Candidate chat completion URLs for OpenAI-compatible providers."""

    return [join_url(base_url, "/chat/completions")]


def anthropic_model_urls(base_url: str) -> list[str]:
    """Candidate model list URLs for Anthropic providers."""

    root_url = strip_known_protocol_suffix(base_url)
    return unique_urls([
        join_url(base_url, "/v1/models"),
        join_url(base_url, "/models"),
        join_url(root_url, "/v1/models"),
        join_url(root_url, "/models"),
    ])


def anthropic_test_urls(base_url: str) -> list[str]:
    """Candidate message URLs for Anthropic providers."""

    return [join_url(base_url, "/v1/messages")]


def responses_model_urls(base_url: str) -> list[str]:
    """Candidate model list URLs for the OpenAI Responses API."""

    return openai_model_urls(base_url)


def responses_test_urls(base_url: str) -> list[str]:
    """Candidate OpenAI Responses URLs."""

    return [join_url(base_url, "/responses")]


def gemini_model_urls(base_url: str) -> list[str]:
    """Google Generative Language model discovery endpoint."""

    base = normalize_base_url(base_url)
    return [join_url(base, "/models") if base.lower().endswith(("/v1", "/v1beta")) else join_url(base, "/v1beta/models")]


def gemini_test_urls(base_url: str, model: str) -> list[str]:
    """Google Gemini generateContent endpoint for a model."""

    base = normalize_base_url(base_url)
    path = f"/models/{quote(model, safe='._-')}:generateContent"
    return [join_url(base, path) if base.lower().endswith(("/v1", "/v1beta")) else join_url(base, "/v1beta" + path)]


def bedrock_model_urls(base_url: str) -> list[str]:
    """Bedrock control-plane foundation-model discovery endpoint."""

    base = normalize_base_url(base_url)
    parsed = urlparse(base)
    host = parsed.netloc.replace("bedrock-runtime.", "bedrock.", 1)
    root = urlunparse((parsed.scheme, host, "", "", "", ""))
    return [join_url(root, "/foundation-models")]


def bedrock_test_urls(base_url: str, model: str) -> list[str]:
    """Bedrock Runtime InvokeModel endpoint for a foundation model."""

    return [join_url(base_url, f"/model/{quote(model, safe='._-')}/invoke")]
