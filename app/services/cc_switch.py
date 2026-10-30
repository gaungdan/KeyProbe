"""Read usable provider configurations from a local CC Switch database."""

from __future__ import annotations

import json
import os
import re
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
# CC Switch 新版把 Claude 的模型选择拆成档位：settings.model 存档位名，
# 实际生效的模型在 env 的 ANTHROPIC_DEFAULT_<档位>_MODEL(_NAME) 里。
_CLAUDE_MODEL_TIERS = ("fable", "sonnet", "opus", "haiku")
# 模型名可能带 CC Switch 自己的上下文标记，例如 deepseek-v4-flash[1M]。
_MODEL_SUFFIX_PATTERN = re.compile(r"\[[^\]]*\]\s*$")
# 下拉框按 app_type 分组展示，顺序沿用 CC Switch 左侧的客户端顺序。
APP_TYPE_ORDER = ("claude", "codex", "gemini", "opencode", "grokbuild")
APP_TYPE_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "gemini": "Gemini",
    "opencode": "OpenCode",
    "grokbuild": "Grok Build",
}


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
    sort_index: int | None = None


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
    for provider_id, app_type, name, settings_raw, meta_raw, sort_index in rows:
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
            providers.append(replace(provider, sort_index=sort_index))

    if not providers:
        return CCSwitchImportResult(
            providers=[],
            source_path=database_path,
            error_message="CC Switch 中没有包含接口地址、密钥和默认模型的可检测配置",
            skipped_count=skipped_count,
        )

    return CCSwitchImportResult(
        providers=_with_display_names(sorted(providers, key=_group_sort_key)),
        source_path=database_path,
        skipped_count=skipped_count,
    )


def _group_sort_key(provider: CCSwitchProvider) -> tuple[int, int, int, str, str]:
    """先按客户端分组（claude/codex/…），组内再按 CC Switch 的 sort_index。"""

    app_type = provider.app_type.strip().lower()
    group_rank = (
        APP_TYPE_ORDER.index(app_type) if app_type in APP_TYPE_ORDER else len(APP_TYPE_ORDER)
    )
    has_index = provider.sort_index is not None
    return (
        group_rank,
        0 if has_index else 1,
        provider.sort_index or 0,
        provider.name.lower(),
        provider.selection_id,
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
    if normalized_app_type == "grokbuild":
        return _parse_grokbuild(provider_id, app_type, name, settings, meta)
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
    model = _claude_model(env, settings)
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


def _claude_model(env: dict[str, Any], settings: dict[str, Any]) -> str:
    """Resolve the model Claude Code actually launches with.

    CC Switch 新版把档位选择存在 settings.model（fable/sonnet/opus/haiku），
    对应模型在 ANTHROPIC_DEFAULT_<TIER>_MODEL 里；旧版只写 ANTHROPIC_MODEL。
    两者都存在时以档位为准，否则导入的模型会和 CC Switch 实际运行的不一致。
    """

    candidates: list[Any] = []
    tier = _text(settings.get("model")).lower()
    if tier in _CLAUDE_MODEL_TIERS:
        upper = tier.upper()
        candidates.append(env.get(f"ANTHROPIC_DEFAULT_{upper}_MODEL_NAME"))
        candidates.append(env.get(f"ANTHROPIC_DEFAULT_{upper}_MODEL"))
    candidates.append(env.get("ANTHROPIC_MODEL"))
    for fallback_tier in _CLAUDE_MODEL_TIERS:
        upper = fallback_tier.upper()
        candidates.append(env.get(f"ANTHROPIC_DEFAULT_{upper}_MODEL_NAME"))
        candidates.append(env.get(f"ANTHROPIC_DEFAULT_{upper}_MODEL"))
    return _strip_model_suffix(_first_text(*candidates))


def _strip_model_suffix(model: str) -> str:
    """Drop CC Switch 的上下文标记，例如 deepseek-v4-flash[1M] -> deepseek-v4-flash。"""

    stripped = _MODEL_SUFFIX_PATTERN.sub("", model).strip()
    return stripped or model


def _parse_grokbuild(
    provider_id: str, app_type: str, name: str, settings: dict[str, Any], meta: dict[str, Any]
) -> CCSwitchProvider | None:
    """Parse Grok CLI style config: [models].default + [model."<key>"]. """

    config_text = _text(settings.get("config"))
    if not config_text:
        return None
    try:
        config = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError:
        return None

    default_key = _text(_mapping(config.get("models")).get("default"))
    entries = _mapping(config.get("model"))
    entry = _mapping(entries.get(default_key)) if default_key else {}
    if not entry:
        entry = next((value for value in entries.values() if isinstance(value, dict)), {})

    backend = _text(entry.get("api_backend")).lower() or _text(meta.get("apiFormat")).lower()
    provider = {
        "responses": OPENAI_RESPONSES,
        "openai_responses": OPENAI_RESPONSES,
        "chat": OPENAI_COMPATIBLE,
        "chat_completions": OPENAI_COMPATIBLE,
        "openai_chat": OPENAI_COMPATIBLE,
        "anthropic": ANTHROPIC,
        "messages": ANTHROPIC,
        "gemini": GOOGLE_GEMINI,
        "google": GOOGLE_GEMINI,
    }.get(backend)
    return _build_provider(
        provider_id,
        app_type,
        name or _text(entry.get("name")),
        _text(entry.get("base_url")),
        _text(entry.get("api_key")),
        provider,
        _url_mode(meta),
        _first_text(entry.get("model"), default_key),
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
            display_name = f"{label} · {_id_suffix(provider.selection_id)}"
        used_labels[display_name] += 1
        if used_labels[display_name] > 1:
            display_name = f"{display_name}-{used_labels[display_name]}"
        labeled.append(replace(provider, display_name=display_name))
    return labeled


def _id_suffix(selection_id: str) -> str:
    """给重名配置一个能区分开的短标识。

    id 较短时直接用整段；较长时（UUID、mycodex-1787292292907 这类时间戳 id）
    取尾部，避免像旧逻辑那样按前 8 位截断后全都变成 "mycodex-"。
    """

    raw_id = selection_id.rsplit(":", 1)[-1]
    return raw_id if len(raw_id) <= 12 else raw_id[-6:]


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
