"""Read usable provider configurations from a local CC Switch database."""

from __future__ import annotations

import json
import os
import sqlite3
import tomllib
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.models.config import UrlMode
from app.services.providers import (
    ANTHROPIC,
    GOOGLE_GEMINI,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
)


CC_SWITCH_DB_NAME = "cc-switch.db"
CC_SWITCH_DIRECTORY = ".cc-switch"
_REQUIRED_COLUMNS = {"id", "app_type", "name", "settings_config", "meta", "sort_index"}


@dataclass(frozen=True)
class CCSwitchProvider:
    """A CC Switch provider normalized for KeyProbe's existing checker."""

    selection_id: str
    display_name: str
    name: str
    app_type: str
    base_url: str
    api_key: str
    provider: str
    url_mode: UrlMode
    model: str


@dataclass(frozen=True)
class CCSwitchImportResult:
    """The outcome of a read-only CC Switch provider import."""

    providers: list[CCSwitchProvider]
    source_path: Path
    error_message: str = ""
    skipped_count: int = 0


def default_cc_switch_db_path() -> Path:
    """Resolve CC Switch's current path and its Windows HOME compatibility path."""

    default_dir = Path.home() / CC_SWITCH_DIRECTORY
    default_path = default_dir / CC_SWITCH_DB_NAME
    if default_path.exists():
        return default_path

    legacy_home = os.environ.get("HOME", "").strip()
    if legacy_home:
        legacy_path = Path(legacy_home) / CC_SWITCH_DIRECTORY / CC_SWITCH_DB_NAME
        if legacy_path.exists():
            return legacy_path
    return default_path


def load_cc_switch_providers(path: Path | None = None) -> CCSwitchImportResult:
    """Load fully usable Claude, Codex, Gemini, and OpenCode entries without mutating CC Switch."""

    database_path = path or default_cc_switch_db_path()
    if not database_path.is_file():
        return CCSwitchImportResult(
            providers=[],
            source_path=database_path,
            error_message=f"未找到 CC Switch 数据库：{database_path}",
        )

    try:
        with closing(_read_only_connection(database_path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(providers)")
            }
            missing_columns = _REQUIRED_COLUMNS - columns
            if missing_columns:
                return CCSwitchImportResult(
                    providers=[],
                    source_path=database_path,
                    error_message="CC Switch 数据库结构不兼容，缺少 providers 字段："
                    + ", ".join(sorted(missing_columns)),
                )
            rows = connection.execute(
                """
                SELECT id, app_type, name, settings_config, meta, sort_index
                FROM providers
                ORDER BY sort_index IS NULL, sort_index, name COLLATE NOCASE, app_type, id
                """
            ).fetchall()
    except sqlite3.Error as exc:
        return CCSwitchImportResult(
            providers=[],
            source_path=database_path,
            error_message=f"读取 CC Switch 数据库失败：{exc}",
        )

    providers: list[CCSwitchProvider] = []
    skipped_count = 0
    for provider_id, app_type, name, settings_raw, meta_raw, _ in rows:
        provider = _parse_provider(
            provider_id=str(provider_id),
            app_type=str(app_type),
            name=str(name),
            settings_raw=settings_raw,
            meta_raw=meta_raw,
        )
        if provider is None:
            skipped_count += 1
        else:
            providers.append(provider)

    if not providers:
        return CCSwitchImportResult(
            providers=[],
            source_path=database_path,
            error_message="CC Switch 中没有包含接口地址、密钥和默认模型的可检测配置",
            skipped_count=skipped_count,
        )

    return CCSwitchImportResult(
        providers=_with_display_names(providers),
        source_path=database_path,
        skipped_count=skipped_count,
    )


def _read_only_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=1)
    connection.execute("PRAGMA query_only = ON")
    return connection


def _parse_provider(
    *,
    provider_id: str,
    app_type: str,
    name: str,
    settings_raw: Any,
    meta_raw: Any,
) -> CCSwitchProvider | None:
    settings = _json_object(settings_raw)
    meta = _json_object(meta_raw)
    if not settings:
        return None

    normalized_app_type = app_type.strip().lower()
    if normalized_app_type == "claude":
        return _parse_claude(provider_id, app_type, name, settings, meta)
    if normalized_app_type == "codex":
        return _parse_codex(provider_id, app_type, name, settings, meta)
    if normalized_app_type == "gemini":
        return _parse_gemini(provider_id, app_type, name, settings, meta)
    if normalized_app_type == "opencode":
        return _parse_opencode(provider_id, app_type, name, settings, meta)
    return None


def _parse_claude(
    provider_id: str, app_type: str, name: str, settings: dict[str, Any], meta: dict[str, Any]
) -> CCSwitchProvider | None:
    env = _mapping(settings.get("env"))
    base_url = _text(env.get("ANTHROPIC_BASE_URL"))
    api_key = _first_text(
        env.get("ANTHROPIC_AUTH_TOKEN"),
        env.get("ANTHROPIC_API_KEY"),
        env.get("OPENROUTER_API_KEY"),
        env.get("GOOGLE_API_KEY"),
    )
    model = _text(env.get("ANTHROPIC_MODEL"))
    api_format = _text(meta.get("apiFormat")).lower() or "anthropic"
    provider = {
        "anthropic": ANTHROPIC,
        "openai_chat": OPENAI_COMPATIBLE,
        "openai_responses": OPENAI_RESPONSES,
    }.get(api_format)
    return _build_provider(
        provider_id, app_type, name, base_url, api_key, provider, _url_mode(meta), model
    )


def _parse_codex(
    provider_id: str, app_type: str, name: str, settings: dict[str, Any], meta: dict[str, Any]
) -> CCSwitchProvider | None:
    config_text = _text(settings.get("config"))
    auth = _mapping(settings.get("auth"))
    if not config_text:
        return None
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return None

    provider_name = _text(config.get("model_provider"))
    provider_config = _mapping(_mapping(config.get("model_providers")).get(provider_name))
    base_url = _text(provider_config.get("base_url"))
    api_key = _text(auth.get("OPENAI_API_KEY"))
    model = _text(config.get("model"))
    wire_api = _text(provider_config.get("wire_api")).lower() or "responses"
    checker_provider = {
        "responses": OPENAI_RESPONSES,
        "chat": OPENAI_COMPATIBLE,
        "chat_completions": OPENAI_COMPATIBLE,
    }.get(wire_api)
    return _build_provider(
        provider_id, app_type, name, base_url, api_key, checker_provider, _url_mode(meta), model
    )


def _parse_gemini(
    provider_id: str, app_type: str, name: str, settings: dict[str, Any], meta: dict[str, Any]
) -> CCSwitchProvider | None:
    env = _mapping(settings.get("env"))
    return _build_provider(
        provider_id,
        app_type,
        name,
        _text(env.get("GOOGLE_GEMINI_BASE_URL")),
        _first_text(env.get("GEMINI_API_KEY"), env.get("GOOGLE_API_KEY")),
        GOOGLE_GEMINI,
        _url_mode(meta),
        _text(env.get("GEMINI_MODEL")),
    )


def _parse_opencode(
    provider_id: str, app_type: str, name: str, settings: dict[str, Any], meta: dict[str, Any]
) -> CCSwitchProvider | None:
    options = _mapping(settings.get("options"))
    npm = _text(settings.get("npm")).lower()
    provider = {
        "@ai-sdk/openai-compatible": OPENAI_COMPATIBLE,
        "@ai-sdk/openai": OPENAI_RESPONSES,
        "@ai-sdk/anthropic": ANTHROPIC,
        "@ai-sdk/google": GOOGLE_GEMINI,
    }.get(npm)
    return _build_provider(
        provider_id,
        app_type,
        name or _text(settings.get("name")),
        _text(options.get("baseURL")),
        _text(options.get("apiKey")),
        provider,
        _url_mode(meta),
        _first_text(*_mapping(settings.get("models"))),
    )


def _build_provider(
    provider_id: str,
    app_type: str,
    name: str,
    base_url: str,
    api_key: str,
    provider: str | None,
    url_mode: UrlMode,
    model: str,
) -> CCSwitchProvider | None:
    if not all((provider_id, name.strip(), base_url, api_key, provider, model)):
        return None
    return CCSwitchProvider(
        selection_id=f"{app_type}:{provider_id}",
        display_name="",
        name=name.strip(),
        app_type=app_type,
        base_url=base_url,
        api_key=api_key,
        provider=provider,
        url_mode=url_mode,
        model=model,
    )


def _with_display_names(providers: list[CCSwitchProvider]) -> list[CCSwitchProvider]:
    base_labels = [f"{provider.name}（{provider.app_type}）" for provider in providers]
    label_counts = Counter(base_labels)
    used_labels: Counter[str] = Counter()
    labeled: list[CCSwitchProvider] = []
    for provider, label in zip(providers, base_labels, strict=True):
        display_name = label
        if label_counts[label] > 1:
            display_name = f"{label} · {provider.selection_id.rsplit(':', 1)[-1][:8]}"
        used_labels[display_name] += 1
        if used_labels[display_name] > 1:
            display_name = f"{display_name}-{used_labels[display_name]}"
        labeled.append(replace(provider, display_name=display_name))
    return labeled


def _url_mode(meta: dict[str, Any]) -> UrlMode:
    return "full" if meta.get("isFullUrl") is True else "auto"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return _mapping(parsed)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def _first_text(*values: Any) -> str:
    return next((text for value in values if (text := _text(value))), "")
