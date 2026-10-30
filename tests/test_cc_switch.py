import asyncio
import json
import sqlite3
from pathlib import Path

import httpx

from app.checker.client import LLMChecker
from app.services import cc_switch
from app.services.cc_switch import load_cc_switch_providers
from app.services.providers import (
    ANTHROPIC,
    GOOGLE_GEMINI,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
)


def _create_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE providers (
                id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                name TEXT NOT NULL,
                settings_config TEXT NOT NULL,
                website_url TEXT,
                category TEXT,
                created_at INTEGER,
                sort_index INTEGER,
                notes TEXT,
                icon TEXT,
                icon_color TEXT,
                meta TEXT NOT NULL DEFAULT '{}',
                is_current BOOLEAN NOT NULL DEFAULT 0,
                in_failover_queue BOOLEAN NOT NULL DEFAULT 0,
                PRIMARY KEY (id, app_type)
            )
            """
        )


def _insert_provider(
    path: Path,
    *,
    provider_id: str,
    app_type: str,
    name: str,
    settings: dict[str, object],
    meta: dict[str, object] | None = None,
    sort_index: int | None = None,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO providers (id, app_type, name, settings_config, meta, sort_index)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                provider_id,
                app_type,
                name,
                json.dumps(settings),
                json.dumps(meta or {}),
                sort_index,
            ),
        )


def test_load_cc_switch_providers_maps_supported_configs_and_keeps_database_unchanged(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    _insert_provider(
        database,
        provider_id="claude-1",
        app_type="Claude",
        name="Relay",
        settings={
            "env": {
                "ANTHROPIC_BASE_URL": "https://claude.example/v1",
                "ANTHROPIC_AUTH_TOKEN": "claude-key",
                "ANTHROPIC_MODEL": "claude-model",
            }
        },
        meta={"apiFormat": "openai_responses", "isFullUrl": True},
        sort_index=2,
    )
    _insert_provider(
        database,
        provider_id="codex-1",
        app_type="Codex",
        name="Relay",
        settings={
            "auth": {"OPENAI_API_KEY": "codex-key"},
            "config": """
model_provider = "custom"
model = "gpt-test"
[model_providers.custom]
base_url = "https://codex.example/v1"
wire_api = "chat"
""",
        },
        sort_index=1,
    )
    _insert_provider(
        database,
        provider_id="gemini-1",
        app_type="Gemini",
        name="Gemini Relay",
        settings={
            "env": {
                "GOOGLE_GEMINI_BASE_URL": "https://gemini.example",
                "GOOGLE_API_KEY": "gemini-key",
                "GEMINI_MODEL": "gemini-test",
            }
        },
        sort_index=3,
    )
    before = database.read_bytes()

    result = load_cc_switch_providers(database)

    assert result.error_message == ""
    assert [item.display_name for item in result.providers] == [
        "Relay（Claude）",
        "Relay（Codex）",
        "Gemini Relay（Gemini）",
    ]
    assert [item.provider for item in result.providers] == [
        OPENAI_RESPONSES,
        OPENAI_COMPATIBLE,
        GOOGLE_GEMINI,
    ]
    assert result.providers[0].url_mode == "full"
    assert database.read_bytes() == before


def test_load_cc_switch_providers_disambiguates_duplicate_labels_and_skips_incomplete_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    valid_settings = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://relay.example",
            "ANTHROPIC_API_KEY": "key",
            "ANTHROPIC_MODEL": "model",
        }
    }
    _insert_provider(
        database,
        provider_id="abcdefgh-1",
        app_type="Claude",
        name="Same",
        settings=valid_settings,
        sort_index=1,
    )
    _insert_provider(
        database,
        provider_id="abcdefgh-2",
        app_type="Claude",
        name="Same",
        settings=valid_settings,
        sort_index=2,
    )
    _insert_provider(
        database,
        provider_id="missing-model",
        app_type="Claude",
        name="Incomplete",
        settings={"env": {"ANTHROPIC_BASE_URL": "https://relay.example", "ANTHROPIC_API_KEY": "key"}},
        sort_index=3,
    )
    _insert_provider(
        database,
        provider_id="unsupported",
        app_type="OpenCode",
        name="Unsupported",
        settings={},
        sort_index=4,
    )

    result = load_cc_switch_providers(database)

    assert [item.display_name for item in result.providers] == [
        "Same（Claude） · abcdefgh-1",
        "Same（Claude） · abcdefgh-2",
    ]
    assert [item.selection_id for item in result.providers] == [
        "Claude:abcdefgh-1",
        "Claude:abcdefgh-2",
    ]
    assert result.skipped_count == 2


def test_load_cc_switch_providers_maps_opencode_first_model(tmp_path: Path) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    _insert_provider(
        database,
        provider_id="opencode-1",
        app_type="opencode",
        name="StepFun",
        settings={
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": "https://api.stepfun.com/step_plan/v1", "apiKey": "step-key"},
            "models": {"step-3.7-flash": {"name": "step-3.7-flash"}, "step-3.5-flash": {}},
        },
        sort_index=1,
    )
    _insert_provider(
        database,
        provider_id="opencode-2",
        app_type="opencode",
        name="No Models",
        settings={
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": "https://relay.example/v1", "apiKey": "key"},
            "models": {},
        },
        sort_index=2,
    )

    result = load_cc_switch_providers(database)

    assert [item.display_name for item in result.providers] == ["StepFun（opencode）"]
    imported = result.providers[0]
    assert imported.provider == OPENAI_COMPATIBLE
    assert imported.base_url == "https://api.stepfun.com/step_plan/v1"
    assert imported.api_key == "step-key"
    assert imported.model == "step-3.7-flash"
    assert imported.url_mode == "auto"
    assert result.skipped_count == 1


def test_load_cc_switch_providers_uses_claude_tier_model_and_strips_context_marker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    _insert_provider(
        database,
        provider_id="claude-tier",
        app_type="claude",
        name="Tiered",
        settings={
            "env": {
                "ANTHROPIC_BASE_URL": "https://relay.example",
                "ANTHROPIC_AUTH_TOKEN": "key",
                "ANTHROPIC_MODEL": "glm-5.3",
                "ANTHROPIC_DEFAULT_FABLE_MODEL": "glm-5.3-flash[1M]",
                "ANTHROPIC_DEFAULT_FABLE_MODEL_NAME": "glm-5.3-flash",
            },
            "model": "fable",
        },
    )
    _insert_provider(
        database,
        provider_id="claude-legacy",
        app_type="claude",
        name="Legacy",
        settings={
            "env": {
                "ANTHROPIC_BASE_URL": "https://legacy.example",
                "ANTHROPIC_AUTH_TOKEN": "key",
                "ANTHROPIC_MODEL": "deepseek-v4-flash[1M]",
            }
        },
    )

    result = load_cc_switch_providers(database)

    models = {item.name: item.model for item in result.providers}
    assert models == {"Tiered": "glm-5.3-flash", "Legacy": "deepseek-v4-flash"}


def test_load_cc_switch_providers_parses_grokbuild_config(tmp_path: Path) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    _insert_provider(
        database,
        provider_id="grok-1",
        app_type="grokbuild",
        name="asia",
        settings={
            "config": """
[models]
default = "grok-4.5"

[model]
[model."grok-4.5"]
model = "grok-4.6"
base_url = "https://grok.example/v1"
name = "asia"
api_backend = "responses"
api_key = "grok-key"
"""
        },
        sort_index=1,
    )
    _insert_provider(
        database,
        provider_id="grok-official",
        app_type="grokbuild",
        name="Grok Official",
        settings={"config": ""},
        sort_index=2,
    )

    result = load_cc_switch_providers(database)

    assert [item.display_name for item in result.providers] == ["asia（grokbuild）"]
    imported = result.providers[0]
    assert imported.provider == OPENAI_RESPONSES
    assert imported.base_url == "https://grok.example/v1"
    assert imported.api_key == "grok-key"
    assert imported.model == "grok-4.6"
    assert result.skipped_count == 1


def test_load_cc_switch_providers_keeps_long_ids_distinguishable(tmp_path: Path) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    settings = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://relay.example",
            "ANTHROPIC_API_KEY": "key",
            "ANTHROPIC_MODEL": "model",
        }
    }
    for index, provider_id in enumerate(
        ["mycodex-1787292292907", "mycodex-1788912930664"], start=1
    ):
        _insert_provider(
            database,
            provider_id=provider_id,
            app_type="claude",
            name="My Claude",
            settings=settings,
            sort_index=index,
        )

    result = load_cc_switch_providers(database)

    labels = [item.display_name for item in result.providers]
    assert labels == ["My Claude（claude） · 292907", "My Claude（claude） · 930664"]


def test_load_cc_switch_providers_groups_by_app_type_before_sort_index(tmp_path: Path) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    claude_settings = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://relay.example",
            "ANTHROPIC_API_KEY": "key",
            "ANTHROPIC_MODEL": "model",
        }
    }
    codex_settings = {
        "auth": {"OPENAI_API_KEY": "key"},
        "config": """
model_provider = "custom"
model = "gpt-test"
[model_providers.custom]
base_url = "https://codex.example/v1"
wire_api = "chat"
""",
    }
    # sort_index 是 CC Switch 各组内部的序号，跨组比较没有意义，这里故意交错
    _insert_provider(
        database,
        provider_id="codex-1",
        app_type="codex",
        name="Codex A",
        settings=codex_settings,
        sort_index=1,
    )
    _insert_provider(
        database,
        provider_id="claude-1",
        app_type="claude",
        name="Claude B",
        settings=claude_settings,
        sort_index=2,
    )
    _insert_provider(
        database,
        provider_id="opencode-1",
        app_type="opencode",
        name="OpenCode C",
        settings={
            "npm": "@ai-sdk/openai-compatible",
            "options": {"baseURL": "https://oc.example/v1", "apiKey": "key"},
            "models": {"oc-model": {}},
        },
        sort_index=3,
    )
    _insert_provider(
        database,
        provider_id="claude-2",
        app_type="claude",
        name="Claude D",
        settings=claude_settings,
        sort_index=1,
    )

    result = load_cc_switch_providers(database)

    assert [item.display_name for item in result.providers] == [
        "Claude D（claude）",
        "Claude B（claude）",
        "Codex A（codex）",
        "OpenCode C（opencode）",
    ]


def test_load_cc_switch_providers_reports_missing_or_incompatible_database(tmp_path: Path) -> None:
    missing = load_cc_switch_providers(tmp_path / "missing.db")
    assert "未找到" in missing.error_message

    incompatible = tmp_path / "incompatible.db"
    with sqlite3.connect(incompatible) as connection:
        connection.execute("CREATE TABLE providers (id TEXT)")

    result = load_cc_switch_providers(incompatible)
    assert "数据库结构不兼容" in result.error_message


def test_cc_switch_provider_reuses_existing_checker_test_flow(tmp_path: Path) -> None:
    database = tmp_path / "cc-switch.db"
    _create_database(database)
    _insert_provider(
        database,
        provider_id="claude-1",
        app_type="Claude",
        name="Anthropic Relay",
        settings={
            "env": {
                "ANTHROPIC_BASE_URL": "https://relay.example",
                "ANTHROPIC_AUTH_TOKEN": "test-key",
                "ANTHROPIC_MODEL": "claude-test",
            }
        },
    )
    imported = load_cc_switch_providers(database).providers[0]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "ok"})

    async def run() -> None:
        result = await LLMChecker(transport=httpx.MockTransport(handler)).test_model(
            provider=imported.provider,
            api_key=imported.api_key,
            model=imported.model,
            timeout=5,
            url_mode=imported.url_mode,
            base_url=imported.base_url,
            model_count=1,
        )
        assert result.status.value == "VALID"

    asyncio.run(run())

    assert str(requests[0].url) == "https://relay.example/v1/messages"
    assert requests[0].headers["x-api-key"] == "test-key"


def test_default_cc_switch_path_uses_legacy_home_only_when_default_is_missing(
    tmp_path: Path, monkeypatch
) -> None:
    default_home = tmp_path / "default"
    legacy_home = tmp_path / "legacy"
    legacy_database = legacy_home / ".cc-switch" / "cc-switch.db"
    legacy_database.parent.mkdir(parents=True)
    legacy_database.touch()
    monkeypatch.setattr(cc_switch.Path, "home", classmethod(lambda cls: default_home))
    monkeypatch.setenv("HOME", str(legacy_home))

    assert cc_switch.default_cc_switch_db_path() == legacy_database

    default_database = default_home / ".cc-switch" / "cc-switch.db"
    default_database.parent.mkdir(parents=True)
    default_database.touch()
    assert cc_switch.default_cc_switch_db_path() == default_database
