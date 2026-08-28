"""Shared auth header construction for every supported provider protocol."""

from __future__ import annotations

import json
from typing import Any

from app.services.bedrock_auth import parse_aws_credentials, sign_bedrock_request
from app.services.providers import AMAZON_BEDROCK, GOOGLE_GEMINI, is_anthropic_provider


def build_headers(
    provider: str,
    api_key: str,
    method: str = "GET",
    url: str = "",
    body: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build request headers for the provider's native auth scheme."""

    if is_anthropic_provider(provider):
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    if provider == GOOGLE_GEMINI:
        return {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    if provider == AMAZON_BEDROCK:
        credentials = parse_aws_credentials(api_key)
        if credentials:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8") if body else b""
            return {
                "Content-Type": "application/json",
                **sign_bedrock_request(
                    credentials=credentials, method=method, url=url, payload=payload
                ),
            }
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
