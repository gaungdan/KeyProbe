"""Build request URLs for auto and full URL modes."""
from collections.abc import Iterable
from urllib.parse import quote, urlparse, urlunparse

KNOWN_COMPAT_SUFFIXES = (
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
)


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


def endpoint_url(base_url: str, path: str) -> str:
    """Append an endpoint path unless the base URL already points at it.

    Users routinely paste a complete endpoint (``.../v1/messages``) into the
    address field. Appending blindly turns it into ``.../v1/messages/v1/messages``,
    which 404s and makes the cURL preview look broken.
    """

    base = normalize_base_url(base_url)
    suffix = "/" + path.strip("/")
    if base.lower().endswith(suffix.lower()):
        return base
    return join_url(base, suffix)


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
    original = base
    lowered = base.lower()
    version_suffix = ""
    if ends_with_version_segment(base):
        index = base.rfind("/")
        version_suffix = base[index:]
        base = base[:index]
        lowered = base.lower()
    for suffix in KNOWN_COMPAT_SUFFIXES:
        if lowered.endswith(suffix):
            return base[: -len(suffix)] + version_suffix
    return original


def ends_with_version_segment(base_url: str) -> bool:
    """Return whether the URL path ends with an OpenAI-style /v{N} segment."""

    last = normalize_base_url(base_url).rsplit("/", 1)[-1]
    return len(last) > 1 and last[0].lower() == "v" and last[1:].isdigit()


def full_url_model_urls(full_url: str) -> list[str]:
    """Derive model-list URLs from a full request URL."""

    url = normalize_base_url(full_url)
    lowered = url.lower()
    marker = "/v1/"
    if marker in lowered:
        index = lowered.find(marker)
        return [url[:index] + "/v1/models"]

    if "/" in url.removeprefix("https://").removeprefix("http://"):
        root = url.rsplit("/", 1)[0]
        if "://" in root and root.rsplit("://", 1)[-1]:
            return [join_url(root, "/v1/models")]

    return []


def openai_model_urls(base_url: str) -> list[str]:
    """Candidate model list URLs for OpenAI-compatible providers.

    Third-party proxies may expose the endpoint at /v1/models or /models.
    """

    if ends_with_version_segment(base_url):
        urls = [join_url(base_url, "/models")]
        if not normalize_base_url(base_url).lower().endswith("/v1"):
            urls.append(join_url(base_url, "/v1/models"))
        return unique_urls(urls)

    return unique_urls([
        join_url(base_url, "/v1/models"),
        join_url(base_url, "/models"),
    ])


def openai_test_urls(base_url: str) -> list[str]:
    """Candidate chat completion URLs for OpenAI-compatible providers."""

    return [endpoint_url(base_url, "/chat/completions")]


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

    return [endpoint_url(base_url, "/v1/messages")]


def responses_model_urls(base_url: str) -> list[str]:
    """Candidate model list URLs for the OpenAI Responses API."""

    return openai_model_urls(base_url)


def responses_test_urls(base_url: str) -> list[str]:
    """Candidate OpenAI Responses URLs."""

    return [endpoint_url(base_url, "/responses")]


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


# ── Shared helpers used by both checker and UI ──────────────────────────


def build_request_body(provider: str, model: str) -> dict[str, object]:
    """Build a minimal request body for the given provider."""

    p = provider.lower()
    if "anthropic" in p or "claude" in p or "协议" in p:
        return {
            "model": model,
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "hello"}],
        }
    if "gemini" in p:
        return {
            "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 16},
        }
    if "bedrock" in p:
        return {
            "messages": [{"role": "user", "content": [{"text": "hello"}]}],
            "inferenceConfig": {"maxTokens": 16, "temperature": 0},
        }
    if "responses" in p or "响应" in p:
        return {"model": model, "input": "hello", "max_output_tokens": 16, "temperature": 0}
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful AI assistant"},
            {"role": "user", "content": "hello"},
        ],
        "temperature": 0,
        "max_tokens": 16,
    }


def pick_test_url(provider: str, base_url: str, model: str, url_mode: str) -> str:
    """Return the first test URL for the given provider/mode."""

    from app.services.providers import (
        AMAZON_BEDROCK,
        GOOGLE_GEMINI,
        OPENAI_RESPONSES,
        is_anthropic_provider,
    )

    if url_mode == "full":
        return normalize_base_url(base_url)
    if is_anthropic_provider(provider):
        return anthropic_test_urls(base_url)[0]
    if provider == AMAZON_BEDROCK:
        return bedrock_test_urls(base_url, model)[0]
    if provider == GOOGLE_GEMINI:
        return gemini_test_urls(base_url, model)[0]
    if provider == OPENAI_RESPONSES:
        return responses_test_urls(base_url)[0]
    return openai_test_urls(base_url)[0]
