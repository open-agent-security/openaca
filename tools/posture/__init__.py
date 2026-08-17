"""Scanner-side posture-finding rules (plan 014, ADR-0009).

Posture findings are emitted by the scanner only — they never become overlay
records, never mint OpenACA IDs, and never change the corpus schema. They
carry a `standards{}` block (CWE / OpenSSF Scorecard / SLSA / OWASP) in
scanner output. Gated behind `--include-posture` to keep the default scan
output strictly vulnerability findings.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.hosts import HOSTS
from tools.parsers.gitignore import is_ignored, load_gitignore_spec
from tools.parsers.settings_layers import load as _load_settings_layers
from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules import (
    api_endpoint_override,
    insecure_transport,
    mcp_auto_approve,
    mutable_install,
    skill_capability,
)

__all__ = [
    "PostureFinding",
    "Standards",
    "collect_endpoint_mcp_manifests",
    "collect_endpoint_posture_inputs",
    "collect_endpoint_settings_manifests",
    "collect_mcp_manifests",
    "collect_settings_manifests",
    "load_manifest_files",
    "run_posture_rules",
]


_MCP_MANIFEST_NAMES: frozenset[str] = frozenset(
    {"mcp.json", ".mcp.json", "claude_desktop_config.json"}
)
_PLUGIN_MANIFEST_NAME = "plugin.json"
_PLUGIN_MANIFEST_PARENT_DIRS = frozenset({".claude-plugin", ".cursor-plugin"})


def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
    settings_manifests: list[tuple[Path, dict]] | None = None,
    manifest_hosts: dict[Path, str] | None = None,
) -> list[PostureFinding]:
    """Run all V0 posture rules and concatenate their findings.

    `manifest_hosts` (built by `collect_endpoint_posture_inputs`) is collection
    provenance: which host's collector actually produced each manifest path.
    When given, it overrides `owning_host(path)` in the manifest-keyed
    host-sensitive rules — the only correct attribution for an explicit
    `--config-dir` root whose basename doesn't match a host's own directory
    convention. `None` (every repo-mode caller) keeps behavior byte-identical.
    """
    settings_manifests = settings_manifests or []
    findings: list[PostureFinding] = []
    findings.extend(mutable_install.check_mutable_install(refs))
    findings.extend(insecure_transport.check_insecure_transport(manifests, manifest_hosts))
    findings.extend(
        mcp_auto_approve.check_mcp_auto_approve(manifests + settings_manifests, manifest_hosts)
    )
    findings.extend(api_endpoint_override.check_api_endpoint_override(settings_manifests))
    findings.extend(skill_capability.check_skill_executable_tools(refs))
    return [_attach_bom_ref(finding, refs) for finding in findings]


def _attach_bom_ref(finding: PostureFinding, refs: list[ComponentRef]) -> PostureFinding:
    if finding.bom_ref is not None or finding.rule_id == "openaca-posture-api-endpoint-override":
        return finding
    declared_path = _declared_path(finding)
    component_type = finding.component.get("type")
    aliases = _finding_aliases(finding)
    matches: list[ComponentRef] = []
    for ref in refs:
        bom_ref = (ref.extra or {}).get("bom_ref")
        if not isinstance(bom_ref, str) or not bom_ref:
            continue
        if (
            isinstance(component_type, str)
            and (ref.extra or {}).get("component_type") != component_type
        ):
            continue
        if declared_path is not None and not _same_path(declared_path, ref.source_manifest):
            continue
        if aliases and not aliases.intersection(_ref_aliases(ref)):
            continue
        matches.append(ref)
    matches_by_bom_ref = {
        str((ref.extra or {})["bom_ref"]): ref
        for ref in matches
        if isinstance((ref.extra or {}).get("bom_ref"), str)
    }
    if len(matches_by_bom_ref) != 1:
        return finding
    bom_ref, matched_ref = next(iter(matches_by_bom_ref.items()))
    component = dict(finding.component)
    identity = canonical_component_identity(matched_ref)
    if identity is None:
        component.pop("identity", None)
    else:
        component["identity"] = identity
    return replace(finding, bom_ref=bom_ref, component=component)


def _declared_path(finding: PostureFinding) -> str | None:
    if not isinstance(finding.declared_by, dict):
        return None
    path = finding.declared_by.get("path")
    return path if isinstance(path, str) and path else None


def _same_path(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, RuntimeError, ValueError):
        return False


def _finding_aliases(finding: PostureFinding) -> set[str]:
    values: list[object] = [finding.component.get("identity"), finding.component.get("name")]
    values.extend(item.get("name") for item in finding.component_path if isinstance(item, dict))
    return {_normalize_alias(value) for value in values if _normalize_alias(value)}


def _ref_aliases(ref: ComponentRef) -> set[str]:
    values: list[object] = [ref.name, ref.component_identity]
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list):
        values.extend(item.get("name") for item in component_path if isinstance(item, dict))
    return {_normalize_alias(value) for value in values if _normalize_alias(value)}


def _normalize_alias(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.split(" @ ", maxsplit=1)[0].strip()
    if normalized.startswith("mcp-server/"):
        normalized = normalized.removeprefix("mcp-server/")
    return normalized


def collect_mcp_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    """Walk one or more roots for MCP-shaped manifests and return parsed dicts.

    Used by URL-shape rules that need the raw manifest to inspect
    `mcpServers[*].url` and adjacent fields. Parse failures are silently
    dropped — these rules are best-effort and should never abort a scan.

    `.git/` is always skipped regardless of `include_gitignored`, consistent
    with the main repo scanner (`parse_repo_grouped`). When
    `include_gitignored=False`, paths matched by `<root>/.gitignore` are also
    skipped, keeping posture scope consistent with the main repo scan.

    A matched path whose resolved real path escapes `root` (a symlink
    planted inside `root` pointing outside it) is dropped rather than read —
    the same containment `tools.parsers.agent_plugins` and
    `claude_plugin_root` enforce for graph realization, so a `root` that is
    a bundle's own closed surface (e.g. a Cursor Agent Plugin's `mcp.json`)
    can't be tricked into attributing an external file to the bundle.
    """
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for name in _MCP_MANIFEST_NAMES:
            for path in root.rglob(name):
                if is_ignored(path.relative_to(root), spec):
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root_resolved):
                    continue
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(data, dict):
                    out.append((path, data))
        for path in root.rglob(_PLUGIN_MANIFEST_NAME):
            if path.parent.name not in _PLUGIN_MANIFEST_PARENT_DIRS:
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root_resolved):
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append((path, data))
    return out


def collect_settings_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for path in root.rglob("settings.json"):
            if path.parent.name != ".claude":
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                out.append((path, data))
    return out


def load_manifest_files(paths: list[Path]) -> list[tuple[Path, dict]]:
    """Read and parse each path as a JSON manifest, in order.

    Missing files and parse failures are silently dropped — the same
    read-parse-skip guard `collect_mcp_manifests` and
    `collect_endpoint_mcp_manifests` use for their direct-path candidates, so
    a malformed or absent manifest never aborts posture collection.
    """
    out: list[tuple[Path, dict]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append((path, data))
    return out


def collect_endpoint_mcp_manifests(
    config_dir: Path,
    project_root: Path | None,
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    """Collect MCP manifests that belong to the resolved endpoint inventory.

    Endpoint mode is install-state-aware. The Claude config directory also
    contains marketplace catalogs and stale cache versions, so recursively
    walking the whole directory would report posture findings for components
    that are not active on the endpoint.
    """
    roots: list[Path] = []
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "plugin":
            continue
        install_path = ref.extra.get("installPath")
        if isinstance(install_path, str) and install_path:
            roots.append(Path(install_path))

    out = collect_mcp_manifests(roots)
    seen = {path.resolve() for path, _ in out}

    direct_paths = [
        config_dir / ".mcp.json",
        config_dir / "mcp.json",
        config_dir / "claude_desktop_config.json",
    ]
    if project_root is not None:
        direct_paths.append(project_root / ".mcp.json")

    for path, data in load_manifest_files(direct_paths):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((path, data))

    return out


def collect_endpoint_posture_inputs(
    host_config_roots: dict[str, Path],
    project_root: Path | None,
    refs: list[ComponentRef],
) -> tuple[list[tuple[Path, dict]], dict[Path, str], list[tuple[Path, dict]]]:
    """Returns (mcp_manifests, manifest_hosts, settings_manifests).

    - mcp_manifests: the concatenation of every selected host's
      collect_endpoint_posture_manifests(root, project_root, refs) output,
      in root-map order, deduped by resolved path (first collector wins).
    - manifest_hosts: each collected path -> the host whose collector
      produced it (collection provenance, for rule attribution).
    - settings_manifests: collect_endpoint_settings_manifests(...) when
      "claude-code" is in the root map, else [] — Cursor has no
      settings.json surface.

    The single shared entry point for both `scan endpoint` and `remote sync
    endpoint`'s collector, so local and uploaded posture findings can't drift.
    """
    mcp_manifests: list[tuple[Path, dict]] = []
    manifest_hosts: dict[Path, str] = {}
    seen: set[Path] = set()
    for host_id, root in host_config_roots.items():
        adapter = HOSTS.get(host_id)
        if adapter is None or adapter.collect_endpoint_posture_manifests is None:
            continue
        for path, data in adapter.collect_endpoint_posture_manifests(root, project_root, refs):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            mcp_manifests.append((path, data))
            manifest_hosts[path] = host_id
    settings_manifests = (
        collect_endpoint_settings_manifests(host_config_roots["claude-code"], project_root)
        if "claude-code" in host_config_roots
        else []
    )
    return mcp_manifests, manifest_hosts, settings_manifests


def collect_endpoint_settings_manifests(
    config_dir: Path,
    project_root: Path | None,
) -> list[tuple[Path, dict]]:
    """Return precedence-merged effective endpoint settings attributed to source scopes.

    Returns one tuple per scope file that owns at least one key in the merged
    effective view. Scalar top-level keys are attributed to the highest-precedence
    scope (local > project > user) that defines that key. Dict-valued top-level
    keys (e.g. ``env``, ``mcpServers``) are split at sub-key granularity so that
    ``env.ANTHROPIC_BASE_URL`` from user scope and ``env.DEBUG`` from local scope
    are each attributed to the file that actually defines them, preventing
    remediation from being misdirected to the wrong settings file.

    The merged effective value is used for every entry (not the raw per-scope
    value) so higher-precedence overrides are honoured and false positives from
    stale lower-scope settings are avoided.
    """
    layers = _load_settings_layers(config_dir, project_root)
    effective = layers.merged(mode="endpoint")
    if not effective:
        return []

    # Scope order: highest precedence first.
    scope_checks: list[tuple[dict | None, Path]] = []
    if project_root is not None:
        scope_checks.append((layers.local, project_root / ".claude" / "settings.local.json"))
        scope_checks.append((layers.project, project_root / ".claude" / "settings.json"))
    scope_checks.append((layers.user, config_dir / "settings.json"))

    path_to_keys: dict[Path, dict] = {}
    for key, merged_value in effective.items():
        if isinstance(merged_value, dict):
            # For dict-valued keys, attribute each sub-key to the
            # highest-precedence scope that defines it so that e.g.
            # env.ANTHROPIC_BASE_URL (user scope) and env.DEBUG (local scope)
            # point to different source files.
            for sub_key, sub_value in merged_value.items():
                source_path = config_dir / "settings.json"  # fallback: user scope
                if (
                    key == "mcpServers"
                    and isinstance(sub_value, dict)
                    and "autoApprove" in sub_value
                ):
                    # Attribute to the scope that owns autoApprove for this
                    # server entry, since that is the field the
                    # mcp_auto_approve rule evaluates as the risk signal.
                    # Falls back to server-name-level attribution if no scope
                    # directly sets autoApprove (e.g. it arrived via deep-merge
                    # from a value that wasn't present in any raw scope).
                    found = False
                    for scope_data, path in scope_checks:
                        if scope_data is None:
                            continue
                        scope_mcp = scope_data.get(key)
                        if not isinstance(scope_mcp, dict):
                            continue
                        server_entry = scope_mcp.get(sub_key)
                        if (
                            isinstance(server_entry, dict)
                            and "autoApprove" in server_entry
                            and path.is_file()
                        ):
                            source_path = path
                            found = True
                            break
                    if not found:
                        for scope_data, path in scope_checks:
                            if scope_data is None:
                                continue
                            scope_dict = scope_data.get(key)
                            if (
                                isinstance(scope_dict, dict)
                                and sub_key in scope_dict
                                and path.is_file()
                            ):
                                source_path = path
                                break
                else:
                    for scope_data, path in scope_checks:
                        if scope_data is None:
                            continue
                        scope_dict = scope_data.get(key)
                        if (
                            isinstance(scope_dict, dict)
                            and sub_key in scope_dict
                            and path.is_file()
                        ):
                            source_path = path
                            break
                path_to_keys.setdefault(source_path, {}).setdefault(key, {})[sub_key] = sub_value
        else:
            source_path = config_dir / "settings.json"  # fallback: user scope
            for scope_data, path in scope_checks:
                if scope_data is not None and key in scope_data and path.is_file():
                    source_path = path
                    break
            path_to_keys.setdefault(source_path, {})[key] = merged_value

    return list(path_to_keys.items())
