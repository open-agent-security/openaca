"""Scanner-side posture-finding rules (plan 014, ADR-0009).

Posture findings are emitted by the scanner only — they never become overlay
records, never mint OpenACA IDs, and never change the corpus schema. They
carry a `standards{}` block (CWE / OpenSSF Scorecard / SLSA / OWASP) in
scanner output. Gated behind `--include-posture` to keep the default scan
output strictly vulnerability findings.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from tools.component_ref import ComponentRef, canonical_component_identity
from tools.parsers.gitignore import is_ignored, load_gitignore_spec
from tools.parsers.settings_layers import load as _load_settings_layers
from tools.posture.finding import PostureFinding, Standards
from tools.posture.rules import (
    api_endpoint_override,
    command_policy_allow,
    insecure_transport,
    mcp_auto_approve,
    mutable_install,
    project_trust,
    skill_capability,
)

__all__ = [
    "KNOWN_RULE_IDS",
    "PostureFinding",
    "Standards",
    "collect_codex_rules_manifests",
    "collect_codex_project_trust_manifests",
    "collect_cursor_endpoint_mcp_manifests",
    "collect_cursor_endpoint_permissions_manifests",
    "collect_cursor_mcp_manifests",
    "collect_cursor_permissions_manifests",
    "collect_endpoint_mcp_manifests",
    "collect_endpoint_settings_manifests",
    "collect_mcp_manifests",
    "collect_settings_manifests",
    "no_manifests",
    "resolve_cursor_permissions",
    "run_posture_rules",
]


# Every rule id `run_posture_rules` can emit. A kind's `posture_rules` allowlist
# is validated against this at kind-construction time, so a typo fails loudly
# instead of silently disabling an intended rule.
KNOWN_RULE_IDS: frozenset[str] = frozenset(
    {
        mutable_install.RULE_ID,
        insecure_transport.RULE_ID,
        mcp_auto_approve.RULE_ID,
        api_endpoint_override.RULE_ID,
        skill_capability.RULE_ID,
        command_policy_allow.RULE_ID,
        project_trust.RULE_ID,
    }
)

_MCP_MANIFEST_NAMES: frozenset[str] = frozenset(
    {"mcp.json", ".mcp.json", "claude_desktop_config.json"}
)
_PLUGIN_MANIFEST_NAME = "plugin.json"
_PLUGIN_MANIFEST_PARENT_DIR = ".claude-plugin"


def no_manifests(*_args: object, **_kwargs: object) -> list[tuple[Path, dict]]:
    """A kind with no filesystem-shaped posture surface yields nothing,
    rather than falling back to another kind's collectors. Shared by every
    caller that needs a collector pair for a kind with no posture surface at
    a given composition source — do not reintroduce a private copy."""
    return []


def run_posture_rules(
    refs: list[ComponentRef],
    manifests: list[tuple[Path, dict]],
    settings_manifests: list[tuple[Path, dict]] | None = None,
    *,
    allowed_rules: frozenset[str] | None = None,
    agent_kind: str | None = None,
    agent_id: str | None = None,
    extra_manifests: Mapping[str, list[tuple[Path, dict]]] | None = None,
) -> list[PostureFinding]:
    """Run all V0 posture rules and concatenate their findings.

    Rule *reach* is structural — an agent's graph holds only its own manifests —
    but *applicability* is declared, because a settings key can mean something
    different, or nothing, in another runtime. `allowed_rules=None` means every
    rule applies.
    """
    settings_manifests = settings_manifests or []
    findings: list[PostureFinding] = []
    findings.extend(mutable_install.check_mutable_install(refs, agent_kind=agent_kind))
    findings.extend(insecure_transport.check_insecure_transport(manifests))
    findings.extend(mcp_auto_approve.check_mcp_auto_approve(manifests + settings_manifests))
    findings.extend(api_endpoint_override.check_api_endpoint_override(settings_manifests))
    findings.extend(skill_capability.check_skill_executable_tools(refs, agent_kind=agent_kind))
    # Surfaces that declare no components and so have no manifest channel of
    # their own. Keyed by rule id rather than positionally: the existing
    # `(mcp, settings)` pair has no room for a third or fourth, and overloading
    # either slot would make one rule's input depend on another's shape.
    extras = extra_manifests or {}
    findings.extend(
        command_policy_allow.check_command_policy_allow(
            extras.get(command_policy_allow.RULE_ID, [])
        )
    )
    findings.extend(project_trust.check_project_trust(extras.get(project_trust.RULE_ID, [])))
    findings = [f for f in findings if allowed_rules is None or f.rule_id in allowed_rules]
    # `active_in` is the answer to "which agent is this active in" (ADR-0044:
    # "the agent doing the scanning is the answer" — see `tools.active_in`).
    # Individual rules infer it from manifest shape (`insecure_transport`,
    # `mcp_auto_approve`) or hardcode it (`api_endpoint_override`) because they
    # predate agent-kind awareness; once the scanning agent is known, it
    # overrides whatever a rule guessed, so a finding's `active_in` never
    # disagrees with its own `agent_kind`.
    findings = [
        replace(
            f,
            agent_kind=agent_kind,
            agent_id=agent_id,
            active_in=[agent_kind] if agent_kind else f.active_in,
        )
        for f in findings
    ]
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
    """The manifest path a candidate ref's `source_manifest` must match,
    or `None` when no such constraint applies.

    Only meaningful when `declared_by.kind == "manifest"` — the finding's
    own source file IS the manifest that also composed the component (e.g.
    an `autoApprove` field inside `mcp.json` itself). A separate policy file
    (Cursor's `permissions.json`, `kind: "permissions"`) names no manifest:
    every one of its findings would otherwise fail this check against every
    candidate ref (a server's `source_manifest` is `mcp.json`, never
    `permissions.json`) and never attach a `bom_ref`, even when the server
    name uniquely identifies a composed component. For those, alias +
    component_type matching alone decides.
    """
    if not isinstance(finding.declared_by, dict):
        return None
    if finding.declared_by.get("kind") != "manifest":
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
    *,
    refs: list[ComponentRef] | None = None,
) -> list[tuple[Path, dict]]:
    """Walk one or more roots for MCP-shaped manifests and return parsed dicts.

    `refs` is accepted and unused: Claude Code's declared walk is already
    congruent with its composition (one plugin format, no realized-subtree
    carve-outs), so there is nothing for graph derivation to correct here. The
    parameter exists so every declared collector shares one signature.

    Used by URL-shape rules that need the raw manifest to inspect
    `mcpServers[*].url` and adjacent fields. Parse failures are silently
    dropped — these rules are best-effort and should never abort a scan.

    `.git/` is always skipped regardless of `include_gitignored`, consistent
    with the main repo scanner (`parse_repo_grouped`). When
    `include_gitignored=False`, paths matched by `<root>/.gitignore` are also
    skipped, keeping posture scope consistent with the main repo scan.
    """
    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for name in _MCP_MANIFEST_NAMES:
            for path in root.rglob(name):
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
        for path in root.rglob(_PLUGIN_MANIFEST_NAME):
            if path.parent.name != _PLUGIN_MANIFEST_PARENT_DIR:
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


def collect_settings_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
    *,
    refs: list[ComponentRef] | None = None,
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

    for path in direct_paths:
        if not path.is_file():
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


# --- Cursor posture surfaces -----------------------------------------------
#
# Cursor's `mcp_auto_approve` posture lives in `permissions.json`, not
# `mcp.json` (docs/specs/cursor-agent-kind.md "Posture rule applicability").
# `insecure_transport`/`mutable_install`/`skill_capability` reuse the shared
# graph and MCP-manifest shapes below unchanged; only the approval surface
# needs its own collector and merge step.


def collect_cursor_mcp_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
    *,
    refs: list[ComponentRef] | None = None,
) -> list[tuple[Path, dict]]:
    """Cursor's declared MCP posture surface, derived from the refs the graph
    already produced — never a directory walk.

    This is the same rule the installed collector follows, and for the same
    reason. Cursor's composition applies a long list of exclusions: realized
    plugin subtrees are off-limits to the direct walk, an Agent Plugins root
    nested under a realized native root never realizes, a bundle missing its
    manifest self-ref realizes nothing at all, gitignored candidates are
    dropped before selection, and the portable format reads only a root
    `mcp.json`. A collector that re-walks has to restate every one of those,
    and each rule it misses reports an `insecure_transport` finding for a
    server the agent never loads.

    Deriving from the graph makes that class of divergence unrepresentable:
    the exclusions are applied once, by the composition builder, and posture
    inherits them by construction rather than by hand.

    `roots` and `include_gitignored` are accepted and unused — the walk they
    parameterised is gone, and the graph they would have searched was already
    built under those same settings.
    """
    del roots, include_gitignored
    return _mcp_manifests_from_refs(refs or [])


def _mcp_manifests_from_refs(refs: list[ComponentRef]) -> list[tuple[Path, dict]]:
    """Reconstruct MCP-shaped manifests from composed `mcp_server` refs.

    Shared by Cursor's declared and installed collectors so the two can never
    disagree about what a composed server looks like to a posture rule.
    """
    by_path: dict[str, dict] = {}
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "mcp_server":
            continue
        source = ref.source_manifest
        name = _mcp_server_ref_name(ref)
        if not source or name is None:
            continue
        entry: dict = {}
        url = (ref.extra or {}).get("url")
        if isinstance(url, str):
            entry["url"] = url
        by_path.setdefault(source, {"mcpServers": {}})["mcpServers"][name] = entry
    return [(Path(source), manifest) for source, manifest in by_path.items()]


def collect_cursor_endpoint_mcp_manifests(
    config_dir: Path,
    project_root: Path | None,
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    """Cursor's installed MCP posture surface, derived from the refs the graph
    already produced — never a directory walk, for the reasons spelled out on
    `collect_cursor_mcp_manifests`. Both Cursor collectors share one
    derivation so declared and installed cannot drift apart.
    """
    del config_dir, project_root
    return _mcp_manifests_from_refs(refs)


def _mcp_server_ref_name(ref: ComponentRef) -> str | None:
    component_path = (ref.extra or {}).get("component_path")
    if isinstance(component_path, list) and component_path:
        last = component_path[-1]
        if isinstance(last, dict) and isinstance(last.get("name"), str):
            return last["name"]
    return ref.name


_CURSOR_PERMISSIONS_FILENAME = "permissions.json"


def _cursor_permissions_config_dir() -> Path:
    """`permissions.json` relocates independently of every other Cursor
    surface (docs/specs/cursor-agent-kind.md "Config root"): `CURSOR_CONFIG_DIR`
    wins when set; otherwise `XDG_CONFIG_HOME` resolves to `<xdg>/cursor`;
    otherwise `~/.cursor`. Every other Cursor surface (`mcp.json`, skills,
    plugins, ...) honors neither variable, so this is deliberately its own
    resolution rather than a shared config-root helper.
    """
    override = os.environ.get("CURSOR_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "cursor"
    return Path.home() / ".cursor"


def _parse_jsonc(text: str) -> object:
    """Parse JSON that may carry `//` and `/* */` comments and trailing
    commas — both documented as supported in `permissions.json`
    (docs/specs/cursor-agent-kind.md "Precedence"). A plain `json.loads`
    raises on a documented-valid file, so this strips comments and trailing
    commas in two string-aware passes (never touching either inside a JSON
    string literal) before handing the result to `json.loads`.
    """
    return json.loads(_strip_trailing_commas(_strip_jsonc_comments(text)))


def _strip_jsonc_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _load_cursor_permissions(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    try:
        data = _parse_jsonc(text)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_cursor_permissions(paths: list[Path]) -> list[tuple[Path, dict]]:
    """One effective view of Cursor's `permissions.json`, concatenated field
    by field across every given path. Both scopes contribute; neither
    replaces the other — Cursor's own reference: "When both exist, Cursor
    concatenates the arrays inside every field. Per-user and per-repo entries
    combine; one does not replace the other." An earlier draft of this
    project's spec described user scope as first-wins per field; that was
    wrong (over-generalized from one sampled code path) and is corrected
    here.

    Returns at most one tuple: `run_posture_rules`' manifest list is
    per-server, so passing the same server name in two separate raw-file
    tuples would double-emit a finding for it. The tuple's own path is a
    fallback only (used when no per-entry source is recorded, e.g. by a
    caller that hand-builds a `cursor_permissions` manifest). Real
    remediation targeting instead comes from `cursor_permissions_sources`,
    a FLAT `name -> path` map recording, for every entry, the last path in
    `paths` that actually declared it — across every field, not per field.
    A name can appear under `mcpAllowlist` in one file and `autoRun` in
    another; attribution has to follow file precedence regardless of which
    field carried the entry, so this is one map updated in `paths` order,
    never a per-field map merged afterwards (a later merge step can't
    recover which file was actually more specific once two different
    fields disagree on it). This means a project-only entry is never
    blamed on the user file (or vice versa) just because the user file
    happens to exist too. "Last path wins attribution" matches the
    existing precedence: a reader fixing an over-permissive entry looks at
    the most specific (project) file first when both declare the same name.

    Source tracking is restricted to `mcp_auto_approve.CURSOR_ALLOW_FIELDS`
    (`mcpAllowlist`, `autoRun`) — the only fields `_check_cursor_permissions`
    evaluates. A name occurring in an unrelated field such as `mcpDenylist`
    must never overwrite the attribution for an allow-field occurrence of the
    same name in an earlier file; that field carries no auto-approval posture
    and its path is not where the risky permission is actually declared.
    """
    merged: dict[str, list] = {}
    sources: dict[str, Path] = {}
    primary: Path | None = None
    for path in paths:
        if not path.is_file():
            continue
        data = _load_cursor_permissions(path)
        if not data:
            continue
        if primary is None:
            primary = path
        for key, value in data.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
                if key in mcp_auto_approve.CURSOR_ALLOW_FIELDS:
                    for entry in value:
                        if isinstance(entry, str):
                            sources[entry] = path
    if primary is None or not merged:
        return []
    return [(primary, {"cursor_permissions": merged, "cursor_permissions_sources": sources})]


def collect_cursor_permissions_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
    *,
    refs: list[ComponentRef] | None = None,
) -> list[tuple[Path, dict]]:
    """Cursor's declared approval-policy surface: `.cursor/permissions.json`
    at any depth under the given roots, honouring `include_gitignored`
    exactly as `collect_mcp_manifests` does. Repo-relative only — like
    `collect_settings_manifests`, a declared scan reports repo content, never
    the scanning machine's home directory; the user-scope file is an
    installed-mode-only concept (`collect_cursor_endpoint_permissions_manifests`).
    A monorepo can declare more than one workspace folder's `permissions.json`;
    all of them concatenate into one effective view via `resolve_cursor_permissions`.

    Unlike the MCP collector this one still walks, because `permissions.json`
    declares no components and so appears in no graph ref. It therefore takes
    the one thing it cannot derive — which subtrees composition already
    claimed — from the graph instead of recomputing it: a realized plugin's
    own fixture content at `.cursor/permissions.json` is not an active policy,
    and Cursor's descent never loads permission files from inside a bundle.
    """
    plugin_roots = _realized_plugin_roots(refs or [])
    paths: list[Path] = []
    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for path in sorted(root.rglob(_CURSOR_PERMISSIONS_FILENAME)):
            if path.parent.name != ".cursor":
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            if _is_under_any(path, plugin_roots):
                continue
            paths.append(path)
    return resolve_cursor_permissions(paths)


def _realized_plugin_roots(refs: list[ComponentRef]) -> list[Path]:
    """Directories the composition graph actually realized as plugins.

    Derived from `plugin` refs rather than a manifest walk, so a manifest that
    qualified for discovery but produced no self-ref — a `plugin.json` with an
    empty `name`, say — never claims a subtree it does not own.
    """
    roots: list[Path] = []
    for ref in refs:
        if (ref.extra or {}).get("component_type") != "plugin":
            continue
        source = ref.source_manifest
        if not source:
            continue
        manifest = Path(source)
        # `<root>/.cursor-plugin/plugin.json` and `<root>/plugin.json` both
        # resolve to `<root>`; a presence-only ref names the bundle directory.
        root = manifest.parent
        if root.name in {".cursor-plugin", ".claude-plugin"}:
            root = root.parent
        elif manifest.is_dir():
            root = manifest
        roots.append(root)
    return roots


def _is_under_any(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def collect_cursor_endpoint_permissions_manifests(
    config_dir: Path,
    project_root: Path | None,
) -> list[tuple[Path, dict]]:
    """Cursor's installed approval-policy surface: the user file (relocated
    per `_cursor_permissions_config_dir`, never derived from `config_dir` —
    see that helper's docstring) plus the project file under
    `project_root/.cursor/permissions.json`, concatenated into one effective
    view.
    """
    del config_dir
    paths = [_cursor_permissions_config_dir() / _CURSOR_PERMISSIONS_FILENAME]
    if project_root is not None:
        paths.append(project_root / ".cursor" / _CURSOR_PERMISSIONS_FILENAME)
    return resolve_cursor_permissions(paths)


# --- Codex posture surfaces (plan 043 Task 10) -----------------------------
#
# Both are **installed-only** and both are read directly from the filesystem
# rather than derived from graph refs. That is the documented exception to
# "posture derives from composition": these surfaces declare no components, so
# there is no ref to derive from. Every other collector still derives.


def collect_codex_rules_manifests(
    config_root: Path,
    project_root: Path | None = None,
    refs: list[ComponentRef] | None = None,
) -> list[tuple[Path, dict]]:
    """Parsed `prefix_rule(...)` entries from `<root>/rules/*.rules`.

    A second, independent read from composition's own: `_record_codex_rules_coverage`
    reads only `unparsed_count` to raise a coverage warning, while this reads
    the rules themselves for finding content. Neither is derivable from the
    other, so the duplication is deliberate rather than an oversight.
    """
    from tools.parsers import codex_rules

    rules_dir = config_root / "rules"
    if not rules_dir.is_dir():
        return []
    out: list[tuple[Path, dict]] = []
    for path in sorted(rules_dir.glob("*.rules")):
        parsed = codex_rules.parse_rules(path)
        if parsed.rules:
            out.append((path, {"rules": parsed.rules}))
    return out


def collect_codex_project_trust_manifests(
    config_root: Path,
    project_root: Path | None = None,
    refs: list[ComponentRef] | None = None,
) -> list[tuple[Path, dict]]:
    """`[projects."<path>"] trust_level` from `<root>/config.toml`."""
    from tools.parsers import codex_config

    config_path = config_root / "config.toml"
    if not config_path.is_file():
        return []
    try:
        config = codex_config.load_config(config_path)
    except Exception:  # noqa: BLE001 - a broken config is a scan gap, not a crash
        return []
    projects = {p.path: p.trust_level for p in config.projects.values()}
    if not projects:
        return []
    return [(config_path, {"projects": projects})]
