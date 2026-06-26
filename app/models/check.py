"""Models for provider checks."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CheckStatus(str, Enum):
    """API key check result categories."""

    VALID = "VALID"
    INVALID_KEY = "INVALID_KEY"
    RATE_LIMITED = "RATE_LIMITED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    UNKNOWN = "UNKNOWN"


class CheckResult(BaseModel):
    """Normalized result returned by the checker service."""

    status: CheckStatus = CheckStatus.UNKNOWN
    provider: str = "OpenAI 兼容接口"
    latency_ms: int | None = None
    model_count: int = 0
    selected_model: str = ""
    error_message: str = ""
    request_url: str = ""
    raw: dict[str, Any] | list[Any] | str = Field(default_factory=dict)


class ModelFetchResult(BaseModel):
    """Models returned from a provider."""

    provider: str = "OpenAI 兼容接口"
    models: list[str] = Field(default_factory=list)
    request_url: str = ""
    latency_ms: int | None = None
    error_message: str = ""
    raw: dict[str, Any] | list[Any] | str = Field(default_factory=dict)
