"""Load and save local app configuration."""

from pathlib import Path

from app.models.config import AppConfig


CONFIG_PATH = Path("config.json")


def load_config(path: Path = CONFIG_PATH) -> AppConfig:
    """Load config from disk, falling back to defaults."""

    if not path.exists():
        return AppConfig()
    try:
        return AppConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return AppConfig()


def save_config(config: AppConfig, path: Path = CONFIG_PATH) -> None:
    """Save config to disk (API key excluded — never persisted)."""

    path.write_text(
        config.model_dump_json(indent=2, exclude={"api_key"}),
        encoding="utf-8",
    )
