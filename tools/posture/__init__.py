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
from dataclasses import replace
from pathlib import Path

from tools.component_ref import ComponentRef, canonical_component_identity
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
    "KNOWN_RULE_IDS",
    "PostureFinding",
    "Standards",
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
) -> list[tuple[Path, dict]]:
    """Cursor's declared MCP-shaped manifest walk: `.cursor/mcp.json` at any
    depth, plus each realized plugin root's bundled MCP manifest.

    Plugin roots are resolved through the SAME ordered candidate list
    Cursor's declared composition builder uses (`find_plugin_roots` /
    `CURSOR_SURFACE.plugin_formats`) rather than a separately hand-rolled
    walk, so a plugin bundled only as `.claude-plugin/plugin.json` still
    reaches posture the same way it reaches composition. An Agent Plugins
    root whose manifest fails Task 2's `validate_manifest` boundary is
    treated as though it were absent — §7.2's own "reject outright" rule —
    since `find_plugin_roots` already committed to that candidate winning
    its directory and there is nothing to fall back to for MCP purposes.
    """
    from tools.graph_build import find_plugin_roots
    from tools.parsers import agent_plugins
    from tools.repo_surface import AGENT_PLUGINS_FORMAT, CURSOR_SURFACE

    out: list[tuple[Path, dict]] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        if not path.is_file():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(data, dict):
            out.append((path, data))

    for root in roots:
        if root is None or not root.exists():
            continue
        spec = None if include_gitignored else load_gitignore_spec(root)
        for path in root.rglob("mcp.json"):
            if path.parent.name != ".cursor":
                continue
            if is_ignored(path.relative_to(root), spec):
                continue
            _add(path)
        for plugin_root, fmt in find_plugin_roots(
            root, CURSOR_SURFACE, include_gitignored=include_gitignored
        ):
            manifest_version = None
            if fmt is AGENT_PLUGINS_FORMAT:
                manifest_path = plugin_root / "plugin.json"
                try:
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not (
                    isinstance(manifest_data, dict)
                    and agent_plugins.validate_manifest(manifest_data)
                ):
                    continue
                manifest_version = agent_plugins.manifest_schema_version(manifest_data)
                if manifest_version is None:
                    continue
            # `agent_plugins._parse_mcp` only ever resolves a plugin-root
            # `mcp.json` (§7.2.1) — never `.mcp.json`, which is a native
            # Cursor-bundle filename with no meaning under Agent Plugins.
            # Trying both names here would let a `.mcp.json` sibling pass a
            # valid envelope check and get reported by posture even though
            # composition never reads it for this format.
            mcp_names = (
                ("mcp.json",)
                if fmt is AGENT_PLUGINS_FORMAT
                else CURSOR_SURFACE.bundled.mcp_filenames
            )
            for mcp_name in mcp_names:
                candidate = plugin_root / mcp_name
                if not candidate.is_file():
                    continue
                if fmt is AGENT_PLUGINS_FORMAT:
                    # A bundled mcp.json that fails §7.2's envelope check is
                    # invisible to composition (`agent_plugins._parse_mcp`
                    # returns [] for it) — apply the identical check here so
                    # posture never reports on servers the graph never loaded.
                    try:
                        mcp_data = json.loads(candidate.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not agent_plugins.validate_mcp_envelope(mcp_data, manifest_version):
                        continue
                _add(candidate)
                break
    return out


def collect_cursor_endpoint_mcp_manifests(
    config_dir: Path,
    project_root: Path | None,
    refs: list[ComponentRef],
) -> list[tuple[Path, dict]]:
    """Cursor's installed MCP posture surface, derived from the refs the
    graph already produced — never a directory walk. A walk would attribute
    a fixture the composition graph never actually loaded (e.g. an Agent
    Plugins bundle's own test fixtures, or a plugin cache entry missing its
    completion sentinel).
    """
    del config_dir, project_root
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
    tuples would double-emit a finding for it. Attributed to the first path
    that actually exists, on the theory that a reader fixing an
    over-permissive entry looks at the most specific (project) file first
    when both are present.
    """
    merged: dict[str, list] = {}
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
    if primary is None or not merged:
        return []
    return [(primary, {"cursor_permissions": merged})]


def collect_cursor_permissions_manifests(
    roots: list[Path],
    include_gitignored: bool = True,
) -> list[tuple[Path, dict]]:
    """Cursor's declared approval-policy surface: `.cursor/permissions.json`
    at any depth under the given roots, honouring `include_gitignored`
    exactly as `collect_mcp_manifests` does. Repo-relative only — like
    `collect_settings_manifests`, a declared scan reports repo content, never
    the scanning machine's home directory; the user-scope file is an
    installed-mode-only concept (`collect_cursor_endpoint_permissions_manifests`).
    A monorepo can declare more than one workspace folder's `permissions.json`;
    all of them concatenate into one effective view via `resolve_cursor_permissions`.
    """
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
            paths.append(path)
    return resolve_cursor_permissions(paths)


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
