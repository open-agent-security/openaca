"""Read native Codex app references and their cached runtime namespace bindings."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers.claude_plugin_root import resolve_within

MAX_FILE_BYTES = 16 * 1024 * 1024
NAMESPACE = re.compile(r"(?:mcp__)?codex_apps__[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*")
APP_ID = re.compile(r"(?:connector_|asdk_app_|templated_apps_)[A-Za-z0-9_-]+")


def read_object(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("app evidence exceeds the size limit")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("app evidence must be an object")
    return value


def remote_install_id(plugin_root: Path) -> str | None:
    path = plugin_root / ".codex-remote-plugin-install.json"
    if not path.is_file():
        return None
    data = read_object(path)
    identifier = data.get("remote_plugin_id")
    if (
        type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(identifier, str)
        or not re.fullmatch(r"plugin_[A-Za-z0-9_-]{1,256}", identifier)
    ):
        raise ValueError("invalid remote plugin installation record")
    return identifier


def app_refs(plugin_root: Path, manifest: dict) -> list[ComponentRef]:
    declared = manifest.get("apps")
    if declared is None:
        return []
    path = resolve_within(plugin_root, declared) if isinstance(declared, str) else None
    if path is None:
        raise ValueError("apps must name a file within the plugin")
    apps = read_object(path).get("apps")
    if not isinstance(apps, dict):
        raise ValueError("apps must be an object")
    refs = []
    for alias, app in apps.items():
        identifier = app.get("id") if isinstance(app, dict) else None
        if (
            not isinstance(alias, str)
            or not alias
            or len(alias) > 256
            or not isinstance(identifier, str)
            or not APP_ID.fullmatch(identifier)
        ):
            raise ValueError("invalid app reference")
        refs.append(
            ComponentRef(
                name=alias,
                source_manifest=str(path),
                source_locator=f"$.apps[{json.dumps(alias)}]",
                extra={
                    "component_type": "mcp_server",
                    "transport": "codex-app",
                    "codex_app_id": identifier,
                    "component_path": [{"type": "mcp_server", "name": alias}],
                },
            )
        )
    return refs


def app_namespaces(config_root: Path) -> dict[str, list[str]]:
    owners: dict[str, set[str]] = {}
    for path in sorted((config_root / "cache/codex_apps_tools").glob("*.json")):
        data = read_object(path)
        if type(data.get("schema_version")) is not int or data["schema_version"] != 4:
            raise ValueError("unsupported app tool cache schema")
        tools = data.get("tools")
        if not isinstance(tools, list):
            raise ValueError("app tool cache must contain a tools list")
        for tool in tools:
            if not isinstance(tool, dict) or tool.get("server_name") != "codex_apps":
                continue
            identifier, namespace = tool.get("connector_id"), tool.get("tool_namespace")
            if (
                isinstance(identifier, str)
                and APP_ID.fullmatch(identifier)
                and isinstance(namespace, str)
                and len(namespace) <= 128
                and NAMESPACE.fullmatch(namespace)
            ):
                owners.setdefault(namespace.removeprefix("mcp__"), set()).add(identifier)
    result: dict[str, list[str]] = {}
    for namespace, identifiers in owners.items():
        if len(identifiers) == 1:
            result.setdefault(next(iter(identifiers)), []).append(namespace)
    return result
