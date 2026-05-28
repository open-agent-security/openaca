from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_API_URL = "https://api.openaca.dev"


class ConfigError(Exception):
    """Raised when Fleet config cannot be parsed without echoing secret values."""


@dataclass(frozen=True)
class FleetConfig:
    api_url: str = DEFAULT_API_URL
    token: str | None = field(default=None, repr=False)
    asset_id: str | None = None


def get_config_path() -> Path:
    return Path.home() / ".config" / "openaca" / "fleet.toml"


def load_fleet_config(path: Path | None = None) -> FleetConfig:
    config_path = path or get_config_path()
    if not config_path.exists():
        return FleetConfig()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"failed to read Fleet config at {config_path}") from exc

    try:
        fleet = data.get("fleet", {})
        api_url = fleet.get("api_url", DEFAULT_API_URL)
        token = fleet.get("token")
        asset_id = fleet.get("asset_id")
    except AttributeError as exc:
        raise ConfigError(f"invalid Fleet config at {config_path}") from exc

    if not isinstance(api_url, str):
        raise ConfigError(f"invalid Fleet config at {config_path}: api_url must be a string")
    if token is not None and not isinstance(token, str):
        raise ConfigError(f"invalid Fleet config at {config_path}: token must be a string")
    if asset_id is not None and not isinstance(asset_id, str):
        raise ConfigError(f"invalid Fleet config at {config_path}: asset_id must be a string")
    return FleetConfig(api_url=api_url, token=token, asset_id=asset_id)


def save_fleet_config(config: FleetConfig, path: Path | None = None) -> None:
    config_path = path or get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = _to_toml(config)
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(config_path, 0o600)


def _to_toml(config: FleetConfig) -> str:
    lines = ["[fleet]", f'api_url = "{_escape(config.api_url)}"']
    if config.token is not None:
        lines.append(f'token = "{_escape(config.token)}"')
    if config.asset_id is not None:
        lines.append(f'asset_id = "{_escape(config.asset_id)}"')
    return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
