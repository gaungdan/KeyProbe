"""Configuration models."""

from typing import Literal

from pydantic import BaseModel, Field


UrlMode = Literal["auto", "full"]
AuditLevel = Literal["quick", "standard", "deep"]


class AppConfig(BaseModel):
    """Persisted application configuration."""

    base_url: str = "https://api.openai.com"
    api_key: str = ""
    url_mode: UrlMode = "auto"
    provider: str = "OpenAl Compatible"
    model: str = ""
    timeout: int = Field(default=30, ge=1, le=300)
    theme_mode: str = "dark"
    audit_level: AuditLevel = "standard"
