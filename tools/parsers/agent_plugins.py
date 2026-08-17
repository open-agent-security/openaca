"""Parse the Agent Plugins open standard's root plugin.json.

Only skills/ and mcp.json are portably standardized across every
compliant client per the v1.0.0 spec (verified directly against
agentplugins/agent-plugins-spec) — commands, agents, hooks, and rules
are explicitly left to client-private `extensions.<reverse-domain>`
namespacing this parser does not read. See ADR-0045 Decision #3.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from tools.component_ref import ComponentRef
from tools.parsers import claude_skill, mcp_json

# The complete authoritative URL shape, not an origin prefix: detection runs
# against every bare plugin.json in a tree (Step 6), so anything looser than
# the exact `/schemas/<version>/plugin.schema.json` path would classify
# unrelated same-origin documents as plugins.
_SCHEMA_RE = re.compile(r"^https://agent-plugins\.org/schemas/[^/]+/plugin\.schema\.json$")


def is_agent_plugins_manifest(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    schema = data.get("$schema")
    return isinstance(schema, str) and _SCHEMA_RE.fullmatch(schema) is not None


def parse(path: Path, runtime_hosts: Optional[list[str]] = None) -> list[ComponentRef]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict):
        return []

    refs: list[ComponentRef] = []
    raw_name = data.get("name")
    name = raw_name if isinstance(raw_name, str) and raw_name else None
    version = data.get("version")
    if not isinstance(version, (str, type(None))):
        version = None
    if name:
        extra: dict = {"component_type": "plugin"}
        if runtime_hosts is not None:
            extra["runtime_hosts"] = runtime_hosts
        refs.append(
            ComponentRef(
                name=name,
                version=version,
                component_identity=f"plugin/{name}",
                source_manifest=str(path),
                source_locator="$",
                extra=extra,
            )
        )

    plugin_root = path.parent
    skills_dir = plugin_root / "skills"
    if skills_dir.is_dir():
        for skill_subdir in sorted(skills_dir.iterdir()):
            skill_md = skill_subdir / "SKILL.md"
            if skill_md.is_file():
                refs.extend(claude_skill.parse(skill_md, runtime_hosts=runtime_hosts))

    mcp_json_path = plugin_root / "mcp.json"
    if mcp_json_path.is_file():
        refs.extend(mcp_json.parse(mcp_json_path, runtime_hosts=runtime_hosts))

    return refs
