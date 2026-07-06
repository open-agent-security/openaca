"""Tier-1 declared-capability extraction from component refs."""

from __future__ import annotations

import re
import shlex
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from tools.capability import Capability
from tools.component_ref import ComponentRef

__all__ = ["declared_capabilities"]

_SKILL_TOOL_CAPABILITIES = {
    "bash": "shell_exec",
    "shell": "shell_exec",
    "write": "file_write",
    "edit": "file_write",
    "read": "file_read",
    "webfetch": "network_egress",
    "websearch": "network_egress",
}

_NETWORK_CLIENTS = frozenset({"curl", "wget", "nc", "scp", "ssh", "httpie", "http", "rsync"})


def _openaca_version() -> str:
    try:
        return version("openaca")
    except PackageNotFoundError:
        return "unknown"


def declared_capabilities(ref: ComponentRef) -> list[Capability]:
    extra = ref.extra or {}
    component_type = extra.get("component_type")
    if component_type == "skill":
        return _skill_capabilities(ref)
    if component_type == "hook":
        return _hook_capabilities(ref)
    if component_type == "mcp_server":
        return _mcp_capabilities(ref)
    return []


def _capability(name: str, execution_locus: str, evidence: dict[str, Any]) -> Capability:
    return Capability(
        name=name,
        execution_locus=execution_locus,
        method="declared",
        source="openaca",
        source_version=_openaca_version(),
        confidence="high",
        evidence=(evidence,),
    )


def _skill_capabilities(ref: ComponentRef) -> list[Capability]:
    frontmatter = _read_frontmatter(Path(ref.source_manifest))
    caps: dict[str, Capability] = {}
    for tool in sorted(_allowed_tools(frontmatter)):
        base = _executable_tool_base(tool).lower()
        name = _SKILL_TOOL_CAPABILITIES.get(base)
        if name is None or name in caps:
            continue
        caps[name] = _capability(
            name,
            "local",
            {
                "kind": "manifest_field",
                "path": ref.source_manifest,
                "field": "allowed-tools",
                "value": tool,
            },
        )
    return list(caps.values())


def _hook_capabilities(ref: ComponentRef) -> list[Capability]:
    command = (ref.extra or {}).get("command")
    if not isinstance(command, str) or not command:
        return []
    caps = [
        _capability(
            "shell_exec",
            "local",
            {"kind": "manifest_field", "path": ref.source_manifest, "field": "command"},
        )
    ]
    client = _network_client(command)
    if client is not None:
        caps.append(
            _capability(
                "network_egress",
                "local",
                {
                    "kind": "manifest_field",
                    "path": ref.source_manifest,
                    "field": "command",
                    "value": client,
                },
            )
        )
    return caps


def _mcp_capabilities(ref: ComponentRef) -> list[Capability]:
    extra = ref.extra or {}
    # `url` is the canonical remote-MCP signal (ADR-0020); `install_source` is
    # a redundant copy the parser populates for posture rules and isn't
    # guaranteed on every ref (e.g. a re-ingested foreign BOM that models the
    # URL without duplicating it into install_source).
    url = extra.get("url")
    if not isinstance(url, str) or not url:
        install_source = extra.get("install_source")
        url = install_source if isinstance(install_source, str) else ""
    if not url.startswith(("http://", "https://")):
        return []
    evidence = {"kind": "manifest_field", "field": "url", "value": url}
    return [
        _capability("network_egress", "remote", dict(evidence)),
        _capability("sensitive_data_access", "remote", dict(evidence)),
    ]


def _network_client(command: str) -> str | None:
    for segment in re.split(r"\||&&|;", command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if not tokens:
            continue
        client = Path(tokens[0]).name
        if client in _NETWORK_CLIENTS:
            return client
    return None


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        loaded = yaml.safe_load(text[3:end].strip())
    except yaml.YAMLError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _allowed_tools(frontmatter: dict[str, Any]) -> set[str]:
    raw = frontmatter.get("allowed-tools")
    if isinstance(raw, str):
        return set(re.findall(r"[^\s,(]+(?:\([^)]*\))?", raw))
    if isinstance(raw, list):
        return {item for item in raw if isinstance(item, str) and item}
    return set()


def _executable_tool_base(tool: str) -> str:
    return tool.split("(", 1)[0].strip()
