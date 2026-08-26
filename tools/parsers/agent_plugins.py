"""Parse a root `plugin.json` conforming to the Agent Plugins standard
(agent-plugins.org/specification) — the portable plugin contract Cursor reads
alongside its own `.cursor-plugin/plugin.json` format.

Two entry points:

- `is_agent_plugins_manifest(path)` — registry guard: does this file even
  declare itself an Agent Plugins manifest? Used to disambiguate a bare
  `plugin.json` pattern from every other file with that name.
- `parse(path)` — the plugin's bundled contract per §7: skills and MCP
  servers only. Commands, agents, hooks, and rules are outside the portable
  contract (§7 enumerates the bundled surfaces; those four are absent) and
  are never walked here, even when present on disk.

§5.2 splits manifest failure in two, and the split drives this module's
shape: any schema violation other than an unknown top-level field or a
non-object `extensions` is fatal — reject the plugin, discover nothing. A
bad bundled `mcp.json` (§7.2.2) is scoped to MCP alone — the plugin's
skills still load.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.component_ref import ComponentRef
from tools.parsers import claude_skill
from tools.parsers.claude_plugin_root import resolve_within
from tools.parsers.mcp_json import parse_mcp_servers

# §5.2: only 1.0.0 is released; 1.1.0 is a Working Draft and not supported.
# Full-match against this allowlist, never an origin-prefix or regex match —
# a permissive match would parse a future 2.0.0 manifest under 1.0.0 rules.
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0.0"})

_MANIFEST_SCHEMA_URL_BY_VERSION = {
    v: f"https://agent-plugins.org/schemas/{v}/plugin.schema.json"
    for v in SUPPORTED_SCHEMA_VERSIONS
}
_MANIFEST_SCHEMA_VERSION_BY_URL = {url: v for v, url in _MANIFEST_SCHEMA_URL_BY_VERSION.items()}
_MCP_SCHEMA_URL_BY_VERSION = {
    v: f"https://agent-plugins.org/schemas/{v}/mcp.schema.json" for v in SUPPORTED_SCHEMA_VERSIONS
}

# §5.5: 1-64 chars, lowercase alphanumerics/hyphens/periods, alphanumeric
# start and end, no consecutive hyphens or periods.
_NAME_RE = re.compile(r"^(?!.*--)(?!.*\.\.)[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")


def is_agent_plugins_manifest(path: Path) -> bool:
    """Registry guard: does `path` declare itself an Agent Plugins manifest?

    True iff the file parses as a JSON object whose `$schema` full-matches a
    supported manifest schema URL. Anything else — unreadable, malformed
    JSON, non-object, missing/unsupported/mismatched `$schema` — is False,
    so a bare `plugin.json` registry pattern doesn't pick up an unrelated
    file of the same name.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    schema = data.get("$schema")
    return isinstance(schema, str) and schema in _MANIFEST_SCHEMA_VERSION_BY_URL


def validate_manifest(data: dict) -> bool:
    """§5.3/§5.5: is a schema-recognized manifest actually a valid plugin?

    Checks `name` and the type of every other permitted field. An unknown
    top-level field and a non-object `extensions` are non-fatal by
    construction — this function never inspects unrecognized fields, so
    their presence (or `extensions`'s shape) cannot fail validation.
    """
    if not isinstance(data, dict):
        return False
    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        return False
    for field in ("version", "description", "homepage", "repository", "license"):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            return False
    author = data.get("author")
    if author is not None:
        if not isinstance(author, dict):
            return False
        for sub_field in ("name", "email", "url"):
            sub_value = author.get(sub_field)
            if sub_value is not None and not isinstance(sub_value, str):
                return False
    keywords = data.get("keywords")
    if keywords is not None:
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            return False
    return True


def parse(path: Path, *, strict: bool = False) -> list[ComponentRef]:
    """Parse a root `plugin.json` into its bundled skills and MCP servers.

    Returns [] on any manifest-level failure (unreadable, malformed JSON,
    non-dict, unsupported `$schema`, or a fatal §5.2 violation) — the plugin
    is rejected outright and no components are discovered. A malformed
    bundled `mcp.json` is scoped to MCP alone (§7.2.2): it costs the
    servers, not the skills.

    `strict=True` raises `ValueError` instead of returning [] on a
    manifest-level failure — same contract as `mcp_json.parse`/
    `claude_skill.parse`'s `strict` flag. The registry-driven route
    (`tools/parsers/__init__.py`) calls with `strict=False` (its default):
    the registry's `is_agent_plugins_manifest` guard runs against arbitrary
    `plugin.json` files, most of which are not Agent Plugins manifests at
    all, so a guard miss must stay silent. `_realize_agent_plugins_root`
    (`tools/graph_build_cursor.py`) calls with `strict=True`: by the time it
    calls `parse`, `_resolve_plugin_format` has already confirmed this exact
    file's `$schema` qualifies, so a subsequent failure here is a real
    defect in a manifest the tooling already committed to treating as Agent
    Plugins — `safe_parse` catches the raise and records it as a warning
    (evidence gap), rather than the scan silently reporting a clean, empty
    composition for a plugin.json it could not actually validate.
    """
    try:
        raw = path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        if strict:
            raise ValueError(f"could not read {path}") from exc
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if strict:
            raise ValueError(f"invalid JSON in {path}") from exc
        return []
    if not isinstance(data, dict):
        if strict:
            raise ValueError(f"{path} is not a JSON object")
        return []
    schema = data.get("$schema")
    if not isinstance(schema, str) or schema not in _MANIFEST_SCHEMA_VERSION_BY_URL:
        if strict:
            raise ValueError(f"{path} has an unsupported or missing $schema")
        return []
    if not validate_manifest(data):
        if strict:
            raise ValueError(f"{path} fails Agent Plugins manifest validation (§5.3/§5.5)")
        return []

    plugin_root = path.parent
    manifest_version = _MANIFEST_SCHEMA_VERSION_BY_URL[schema]
    name = data["name"]
    version = data.get("version") if isinstance(data.get("version"), str) else None
    refs: list[ComponentRef] = [_plugin_self_ref(name, version, path)]
    refs.extend(_parse_skills(plugin_root))
    refs.extend(_parse_mcp(plugin_root, manifest_version))
    return refs


def _plugin_self_ref(name: str, version: str | None, path: Path) -> ComponentRef:
    """Plugin self-identity, matching `claude_plugin.py`'s ref shape.

    `plugin/{name}` exactly — never a format-qualified prefix. Per
    ADR-0016/`tools/identity.py`, any identity string with two or more `/`
    grants cross-BOM identity with no provenance check, so both plugin
    manifest formats must converge on the same unqualified identity for a
    plugin of the same name to be recognized as the same component.
    """
    return ComponentRef(
        name=name,
        version=version,
        component_identity=f"plugin/{name}",
        source_manifest=str(path),
        source_locator="$",
        extra={"component_type": "plugin"},
    )


def _parse_skills(plugin_root: Path) -> list[ComponentRef]:
    """§7.1: immediate child directories of `skills/` holding a `SKILL.md`.

    One level deep only — clients MUST NOT recurse into deeper descendants.
    This is the inverse of Cursor's own (recursive) skill roots, so it
    deliberately does not share a walker with them.
    """
    skills_dir = resolve_within(plugin_root, "skills")
    if skills_dir is None or not skills_dir.is_dir():
        return []
    try:
        entries = sorted(skills_dir.iterdir())
    except OSError:
        return []

    refs: list[ComponentRef] = []
    for entry in entries:
        skill_dir = resolve_within(plugin_root, f"skills/{entry.name}")
        if skill_dir is None or not skill_dir.is_dir():
            continue
        skill_md = resolve_within(plugin_root, f"skills/{entry.name}/SKILL.md")
        if skill_md is None or not skill_md.is_file():
            continue
        refs.extend(claude_skill.parse(skill_md))
    return refs


def manifest_schema_version(data: dict) -> str | None:
    """The supported schema version a validated manifest's `$schema` names,
    or `None`. Exported so a caller that already has a validated `plugin.json`
    dict (e.g. posture's plugin-root walk) can look up the matching MCP
    schema version without a private cross-module import.
    """
    schema = data.get("$schema")
    return _MANIFEST_SCHEMA_VERSION_BY_URL.get(schema) if isinstance(schema, str) else None


def validate_mcp_envelope(data: object, manifest_version: str | None) -> bool:
    """§7.2.1/§7.2.2: is `data` a valid bundled `mcp.json` envelope for
    `manifest_version`? Exactly `{"$schema", "mcpServers"}` as top-level
    keys, `$schema` matching the schema URL for `manifest_version`, and
    `mcpServers` a dict. `manifest_version=None` (an unrecognized manifest
    `$schema`) always fails — there is no schema URL to match against.

    Exported so posture's `collect_cursor_mcp_manifests` can apply the
    identical check before treating a bundled `mcp.json` as posture-relevant:
    a malformed envelope is invisible to composition (§7.2.2 scopes the
    failure to MCP alone, not the whole plugin) and must stay invisible to
    posture too, or posture reports on servers the graph never composed.
    """
    if manifest_version is None:
        return False
    if not isinstance(data, dict):
        return False
    if set(data.keys()) - {"$schema", "mcpServers"}:
        return False
    schema = data.get("$schema")
    expected_schema = _MCP_SCHEMA_URL_BY_VERSION.get(manifest_version)
    if not isinstance(schema, str) or expected_schema is None or schema != expected_schema:
        return False
    return isinstance(data.get("mcpServers"), dict)


def _parse_mcp(plugin_root: Path, manifest_version: str) -> list[ComponentRef]:
    """§7.2.1/§7.2.2: validate the `mcp.json` envelope, then hand the inner
    `mcpServers` map to the shared MCP dispatch for per-entry parsing.

    Any envelope failure — missing file, invalid JSON, or a shape
    `validate_mcp_envelope` rejects (non-object, wrong/missing `$schema`, a
    `$schema` version that doesn't match `plugin.json`, an extra top-level
    field, non-dict `mcpServers`) — disables MCP for this plugin and returns
    []. It never affects the plugin's skills, which are parsed separately.
    """
    mcp_path = resolve_within(plugin_root, "mcp.json")
    if mcp_path is None or not mcp_path.is_file():
        return []
    try:
        raw = mcp_path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not validate_mcp_envelope(data, manifest_version):
        return []
    return parse_mcp_servers(
        data["mcpServers"],
        source_manifest=str(mcp_path),
        locator_prefix="$.mcpServers",
    )
