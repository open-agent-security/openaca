"""Renderers for `openaca-scan` output: text (default), github, json.

The CLI dispatches to one of three renderers based on `--format`:

- `render_text` — grouped by package, severity per finding, ANSI-colored
  when stdout is a TTY. Default for terminal use.
- `render_github` — GitHub workflow annotation lines (`::error file=...::`).
  Auto-selected when `GITHUB_ACTIONS=true`; CI parses these into PR-side
  annotations.
- `render_json` — structured records keyed by finding; for tool consumption.

Severity labels come from `tools.severity.derive_severity_label` (upstream
GHSA label wins; otherwise compute from CVSS vector; otherwise UNKNOWN).

The grouping shape (one block per `(ecosystem, name, version, manifest)`
tuple, severity per finding) is the format the user converged on. A package
with two HIGH advisories shows as one group with two rows, not two
near-identical blocks.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from packaging.version import InvalidVersion, Version

from tools.component_ref import ComponentRef
from tools.matcher import Finding
from tools.severity import derive_severity_label, derive_severity_score


@dataclass
class ScanStats:
    """Scan-level totals consumed by every renderer for the footer block.

    `unit_label` distinguishes repo-mode (`"manifest"`) from endpoint-mode
    (`"active plugin"`) so the human-facing footer reads accurately for each
    scan type. Pluralization is applied at render time.
    """

    unit_count: int = 0
    unit_label: str = "manifest"
    component_count: int = 0
    parse_failed: int = 0
    sources: set[str] = field(default_factory=set)


_SEVERITY_RANK = {
    "CRITICAL": 5,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2,
    "NONE": 1,
    "UNKNOWN": 0,
}

_SEVERITY_COLOR = {
    "CRITICAL": "\x1b[1;31m",  # bold red
    "HIGH": "\x1b[31m",  # red
    "MEDIUM": "\x1b[33m",  # yellow
    "LOW": "\x1b[34m",  # blue
    "NONE": "\x1b[2m",  # dim
    "UNKNOWN": "\x1b[2m",
}
_RESET = "\x1b[0m"


# ── Helpers shared across formats ────────────────────────────────────────────


def _severity_rank(label: str) -> int:
    return _SEVERITY_RANK.get(label, 0)


def _label_for(finding: Finding, advisory_index: dict[str, dict]) -> str:
    return derive_severity_label(advisory_index.get(finding.advisory_id) or {})


def _color(label: str, use_color: bool) -> str:
    if not use_color:
        return label
    code = _SEVERITY_COLOR.get(label, "")
    if not code:
        return label
    return f"{code}{label}{_RESET}"


def _fixed_in_for_finding(finding: Finding, advisory: dict) -> Optional[str]:
    """Return the `fixed` version for the window that contains the component version.

    For advisories with multiple introduced/fixed windows (e.g. backported
    patches), returns the fix closing the *matched* window rather than the
    first fixed event, which may belong to a prior patch series the component
    has already surpassed.

    Falls back to the first fixed event when the component version is not
    parseable (low/unknown confidence findings cannot be range-narrowed).
    """
    eco = finding.component.ecosystem
    name = finding.component.name
    try:
        version: Optional[Version] = Version(finding.component.version or "")
    except (InvalidVersion, TypeError):
        version = None

    raw_fallback: Optional[str] = None

    for entry in advisory.get("affected") or []:
        if not isinstance(entry, dict):
            continue
        pkg = entry.get("package") or {}
        if pkg.get("ecosystem") != eco or pkg.get("name") != name:
            continue
        for r in entry.get("ranges") or []:
            intro: Optional[str] = None
            for ev in r.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                if "introduced" in ev:
                    intro = ev["introduced"]
                elif "fixed" in ev and intro is not None:
                    if version is None:
                        # Unparseable version — can't narrow to a window; return first fixed.
                        return str(ev["fixed"])
                    try:
                        intro_v = Version("0") if intro == "0" else Version(intro)
                        fixed_v = Version(ev["fixed"])
                    except InvalidVersion:
                        # Non-PEP440 boundary (e.g. npm prerelease like 1.0.0-beta.1) —
                        # can't range-narrow; record raw fixed string as fallback so the
                        # per-finding row shows "fixed in X" rather than "fixed in no fix".
                        if raw_fallback is None:
                            raw_fallback = str(ev["fixed"])
                        intro = None
                        continue
                    if version >= intro_v and version < fixed_v:
                        return str(ev["fixed"])
                    intro = None
                else:
                    # last_affected or limit — close window, no parseable fix target.
                    intro = None
    return raw_fallback


def _summary_for_advisory(advisory: dict) -> str:
    """Single-line summary text for the right column of a finding row."""
    text = advisory.get("summary") or advisory.get("details") or advisory.get("id") or ""
    # Collapse newlines so a multi-line `details` block doesn't break the
    # one-row-per-finding format. Truncation is deliberately not applied —
    # terminals handle long lines fine; truncation only loses information.
    return " ".join(text.split())


def _source_for_advisory(advisory: dict) -> Optional[str]:
    ds = advisory.get("database_specific") or {}
    if not isinstance(ds, dict):
        return None
    openaca = ds.get("openaca") or {}
    if not isinstance(openaca, dict):
        return None
    src = openaca.get("source")
    return src if isinstance(src, str) else None


def _component_label(ref: ComponentRef) -> tuple[str, str]:
    """Return (name_part, version_part) for the group header. Empty strings
    when a field is missing — caller decides spacing."""
    if ref.name and ref.version:
        return ref.name, ref.version
    if ref.name:
        return ref.name, ""
    if ref.component_identity:
        return ref.component_identity, ""
    return "<unidentified>", ""


# ── Grouping ─────────────────────────────────────────────────────────────────


GroupKey = tuple[str, str, str, str]


def _group_key(finding: Finding) -> GroupKey:
    c = finding.component
    return (
        c.ecosystem or "",
        c.name or c.component_identity or "",
        c.version or "",
        str(c.source_manifest or ""),
    )


def _group_findings(findings: list[Finding]) -> "OrderedDict[GroupKey, list[Finding]]":
    """Group findings by (ecosystem, name, version, manifest). Insertion-order
    preserved; the caller re-sorts for display."""
    groups: OrderedDict[GroupKey, list[Finding]] = OrderedDict()
    for f in findings:
        groups.setdefault(_group_key(f), []).append(f)
    return groups


def _aggregate_fix(
    findings_in_group: list[Finding], advisory_index: dict[str, dict]
) -> Optional[str]:
    """The version a user must reach to clear ALL advisories in the group.

    Returns the maximum `fixed` version across every advisory in the group
    using semver-aware ordering. Returns None when at least one advisory
    declares no fix (no single upgrade clears the group) or when any version
    string fails to parse as a Version (mixing semver schemes; caller falls
    back to a 'see findings' message).
    """
    fixed_versions: list[str] = []
    for f in findings_in_group:
        adv = advisory_index.get(f.advisory_id)
        if not adv:
            return None
        v = _fixed_in_for_finding(f, adv)
        if v is None:
            return None
        fixed_versions.append(v)
    if not fixed_versions:
        return None
    try:
        parsed = [Version(v) for v in fixed_versions]
    except InvalidVersion:
        return None
    return str(max(parsed))


# ── Text renderer ────────────────────────────────────────────────────────────


def render_text(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    stats: ScanStats,
    *,
    use_color: bool = False,
    verbose: bool = False,
) -> str:
    """Grouped human-readable output. See module docstring for shape."""
    unit_phrase = _pluralize(stats.unit_count, stats.unit_label)
    component_phrase = _pluralize(stats.component_count, "component")
    if not findings:
        parts = [f"Scanned {unit_phrase}, {component_phrase}"]
        if stats.parse_failed:
            parts.append(f"({stats.parse_failed} failed to parse)")
        parts.append("— no findings.")
        # ACA framing footer: keep users from concluding "OpenACA found
        # nothing" when their general software deps weren't even in scope.
        return " ".join(parts) + (
            "\nOpenACA scans agent composition; for general software dependency "
            "scans, use a general-purpose SCA scanner."
        )

    groups = _group_findings(findings)
    ranked: list[tuple[int, str, GroupKey, list[Finding]]] = []
    for key, group_findings in groups.items():
        max_rank = max(_severity_rank(_label_for(f, advisory_index)) for f in group_findings)
        ranked.append((max_rank, key[1], key, group_findings))
    # Severity desc, then component name asc, then version asc (stable).
    ranked.sort(key=lambda t: (-t[0], t[1]))

    out: list[str] = []
    n_pkgs = len(groups)
    out.append(
        f"Found {len(findings)} "
        f"vulnerabilit{'y' if len(findings) == 1 else 'ies'} in "
        f"{n_pkgs} package{'' if n_pkgs == 1 else 's'}."
    )
    out.append("")

    for _, _, key, group_findings in ranked:
        _, _, _, source_manifest = key
        first = group_findings[0]
        name_part, version_part = _component_label(first.component)
        if version_part:
            out.append(f"{name_part} {version_part}")
        else:
            out.append(name_part)

        out.append(f"  location: {source_manifest}")

        attributed = first.attributed_to
        if attributed:
            out.append(f"  via:      {attributed}")
            out.append(f"  fix:      upgrade or remove {attributed}")
        else:
            agg = _aggregate_fix(group_findings, advisory_index)
            if agg is not None:
                out.append(f"  fix:      upgrade to >={agg}")
            else:
                out.append("  fix:      see findings")
        out.append("")

        findings_sorted = sorted(
            group_findings,
            key=lambda f: (-_severity_rank(_label_for(f, advisory_index)), f.advisory_id),
        )
        for f in findings_sorted:
            adv = advisory_index.get(f.advisory_id) or {}
            label = derive_severity_label(adv)
            label_disp = _color(label, use_color)
            fixed_in = _fixed_in_for_finding(f, adv) or "no fix"
            summary = _summary_for_advisory(adv)
            source = _source_for_advisory(adv) or "openaca.dev"
            out.append(
                f"  {label_disp}  {f.advisory_id}  fixed in {fixed_in}  {summary}  [{source}]"
            )
            if verbose:
                ds_openaca = (adv.get("database_specific") or {}).get("openaca") or {}
                if not isinstance(ds_openaca, dict):
                    ds_openaca = {}
                taxonomies = ds_openaca.get("taxonomies") or {}
                if isinstance(taxonomies, dict):
                    taxonomy_parts = []
                    for family, values in sorted(taxonomies.items()):
                        if isinstance(values, list) and values:
                            taxonomy_parts.append(f"{family}={','.join(str(v) for v in values)}")
                    if taxonomy_parts:
                        out.append(f"        taxonomies: {'; '.join(taxonomy_parts)}")
                evidence_level = ds_openaca.get("evidence_level")
                if isinstance(evidence_level, str):
                    out.append(f"        evidence_level: {evidence_level}")
                out.append(f"        confidence: {f.confidence}")
        out.append("")

    sources_str = " + ".join(sorted(stats.sources)) if stats.sources else "(none)"
    parse_note = f" ({stats.parse_failed} failed to parse)" if stats.parse_failed else ""
    out.append(f"Scanned {unit_phrase}, {component_phrase}{parse_note}. Sources: {sources_str}.")
    return "\n".join(out)


def _pluralize(count: int, label: str) -> str:
    """`1 manifest` vs `2 manifests`. Handles trailing-s correctly for the
    two labels we use (`manifest`, `active plugin`, `component`)."""
    if count == 1:
        return f"{count} {label}"
    return f"{count} {label}s"


# ── GitHub workflow-command renderer ─────────────────────────────────────────


def _esc_param(value: str) -> str:
    """Percent-encode a workflow-command parameter value per GitHub docs."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _esc_data(value: str) -> str:
    """Percent-encode a workflow-command message value per GitHub docs."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render_github(findings: list[Finding]) -> str:
    """Emit one workflow-annotation line per finding. Matcher `confidence`
    maps to `::error` (high) or `::warning` (low/unknown). Returns the joined
    string; caller prints to stdout."""
    level_for = {"high": "error", "low": "warning", "unknown": "warning"}
    lines: list[str] = []
    for f in findings:
        kind = level_for.get(f.confidence, "warning")
        file_param = _esc_param(str(f.component.source_manifest))
        title_param = _esc_param(f.advisory_id)
        message = f.reason or f.advisory_id
        if f.attributed_to:
            message = f"{message} (via {f.attributed_to})"
        lines.append(f"::{kind} file={file_param},title={title_param}::{_esc_data(message)}")
    return "\n".join(lines)


# ── JSON renderer ────────────────────────────────────────────────────────────


def render_json(findings: list[Finding], advisory_index: dict[str, dict], stats: ScanStats) -> str:
    """Structured per-finding records + scan-level stats. The schema is
    documented in README; consumers should treat unknown keys as forward-
    compatible additions, not as a stability break."""
    out_findings = []
    for f in findings:
        adv = advisory_index.get(f.advisory_id) or {}
        out_findings.append(
            {
                "id": f.advisory_id,
                "severity": derive_severity_label(adv),
                "score": derive_severity_score(adv),
                "confidence": f.confidence,
                "package": {
                    "ecosystem": f.component.ecosystem,
                    "name": f.component.name,
                    "version": f.component.version,
                },
                "location": str(f.component.source_manifest),
                "fixed_in": _fixed_in_for_finding(f, adv),
                "summary": _summary_for_advisory(adv) or None,
                "source": _source_for_advisory(adv),
                "attributed_to": f.attributed_to,
            }
        )
    document = {
        "findings": out_findings,
        "stats": {
            "unit": stats.unit_label,
            "units": stats.unit_count,
            "components": stats.component_count,
            "parse_failed": stats.parse_failed,
            "high_confidence": sum(1 for f in findings if f.confidence == "high"),
            "sources": sorted(stats.sources),
        },
    }
    return json.dumps(document, indent=2, sort_keys=False)


# ── Inventory tree (verbose endpoint/repo output) ────────────────────────────


_TREE_UNICODE = {"branch": "├── ", "last": "└── ", "vert": "│   ", "space": "    "}
_TREE_ASCII = {"branch": "|-- ", "last": "`-- ", "vert": "|   ", "space": "    "}

# Bundled-component ecosystem → category label mapping. Order matters: the
# tree renders categories in this declared order so all plugins read the same
# way (MCPs first, agents last).
_TREE_CATEGORIES: tuple[tuple[str, set[str]], ...] = (
    ("MCPs", {"npm", "PyPI"}),
    ("skills", {"claude-skill"}),
    ("hooks", {"claude-hook"}),
    ("commands", {"claude-command"}),
    ("agents", {"claude-agent"}),
)


@dataclass
class _TreeNode:
    label: str
    children: list["_TreeNode"] = field(default_factory=list)


def _ref_key(ref: ComponentRef) -> tuple:
    """Identity used to look up findings against a given component ref.

    Combines the most stable identifiers the matcher would have produced —
    enough to dedupe across discovery paths while distinguishing the same
    package at different versions or in different manifests.
    """
    return (
        ref.ecosystem or "",
        ref.name or "",
        ref.version or "",
        str(ref.source_manifest or ""),
        ref.component_identity or "",
    )


def _findings_by_ref(findings: list[Finding]) -> dict[tuple, list[str]]:
    out: dict[tuple, list[str]] = {}
    for f in findings:
        out.setdefault(_ref_key(f.component), []).append(f.advisory_id)
    return out


def _finding_marker(ids: list[str], use_color: bool) -> str:
    """Render the `[! <ids>]` suffix that flags an affected leaf or plugin
    header. Returns the empty string when no findings matched."""
    if not ids:
        return ""
    text = f"  [! {', '.join(sorted(set(ids)))}]"
    if not use_color:
        return text
    return f"\x1b[31m{text}\x1b[0m"


def _leaf_label(ref: ComponentRef, parent_plugin: Optional[str] = None) -> str:
    """Short identifier rendered on a tree leaf.

    Strips the ecosystem prefix (`claude-skill/`, `claude-command/`, etc.) —
    the parent category label already states the kind. Hooks use their
    observation metadata for display so users see the configured command
    instead of the logical identity hash.
    """
    if ref.ecosystem in {"npm", "PyPI"}:
        if ref.name and ref.version:
            return f"{ref.name}@{ref.version}"
        return ref.name or "<unnamed>"
    if ref.ecosystem == "claude-hook":
        command = ref.extra.get("command")
        event = ref.extra.get("event")
        index = ref.extra.get("index")
        label = command if isinstance(command, str) and command else ref.component_identity
        if isinstance(event, str) and isinstance(index, int):
            return f"{event}[{index}]: {label}"
        return label or "<hook>"
    if ref.component_identity:
        ident = ref.component_identity
        # Strip ecosystem prefix (e.g., `claude-command/`).
        first_slash = ident.find("/")
        if first_slash > 0 and ident.startswith("claude-"):
            ident = ident[first_slash + 1 :]
        return ident
    if ref.name:
        return ref.name
    return "<unidentified>"


def _bundled_categories(
    refs: list[ComponentRef], plugin_identity: str
) -> dict[str, list[ComponentRef]]:
    """Group a plugin's bundled refs by category. Tier-2 lockfile/manifest
    refs (those with `extra["transitive"]` set) are excluded — they aggregate
    into a separate deps line, not the bundled category leaves."""
    by_cat: dict[str, list[ComponentRef]] = {label: [] for label, _ in _TREE_CATEGORIES}
    for r in refs:
        if r.attributed_to != plugin_identity:
            continue
        if r.extra.get("transitive") is not None:
            continue
        for label, ecos in _TREE_CATEGORIES:
            if r.ecosystem in ecos:
                by_cat[label].append(r)
                break
    return {k: v for k, v in by_cat.items() if v}


def _tier2_summary(
    refs: list[ComponentRef], plugin_identity: str
) -> list[tuple[str, str, int, str, list[ComponentRef]]]:
    """Aggregate Tier-2 refs per ecosystem. Returns a list of
    (ecosystem, coverage_kind, count, source_file, ecorefs) so the tree can
    render one node per ecosystem instead of hundreds of transitive leaves.
    `ecorefs` lets callers check findings against the individual refs."""
    by_eco: dict[str, list[ComponentRef]] = {}
    for r in refs:
        if r.attributed_to != plugin_identity:
            continue
        if r.extra.get("transitive") is None:
            continue
        by_eco.setdefault(r.ecosystem or "", []).append(r)
    out: list[tuple[str, str, int, str, list[ComponentRef]]] = []
    for eco in sorted(by_eco):
        ecorefs = by_eco[eco]
        transitive = any(r.extra.get("transitive") is True for r in ecorefs)
        if transitive:
            kind = "transitive"
            source = "package-lock.json" if eco == "npm" else "uv.lock"
        else:
            kind = "direct only"
            source = "package.json" if eco == "npm" else "pyproject.toml"
        out.append((eco, kind, len(ecorefs), source, ecorefs))
    return out


def _bare_categories(refs: list[ComponentRef]) -> dict[str, list[ComponentRef]]:
    """Group refs with `attributed_to is None` by category (no plugin parent)."""
    by_cat: dict[str, list[ComponentRef]] = {label: [] for label, _ in _TREE_CATEGORIES}
    for r in refs:
        if r.attributed_to is not None:
            continue
        if r.ecosystem == "claude-plugin":
            continue
        for label, ecos in _TREE_CATEGORIES:
            if r.ecosystem in ecos:
                by_cat[label].append(r)
                break
    return {k: v for k, v in by_cat.items() if v}


def _plugin_name_from_identity(plugin_identity: str) -> str:
    """Extract `<plugin>` from `claude-plugin/<plugin>[@version]`. Returns the
    empty string when the identity doesn't match that shape."""
    if not plugin_identity.startswith("claude-plugin/"):
        return ""
    rest = plugin_identity[len("claude-plugin/") :]
    at = rest.rfind("@")
    return rest if at < 0 else rest[:at]


def _build_plugin_node(
    plugin_ref: ComponentRef,
    all_refs: list[ComponentRef],
    findings_by_ref: dict[tuple, list[str]],
    use_color: bool,
) -> _TreeNode:
    sha = plugin_ref.extra.get("gitCommitSha")
    sha_note = f" (sha: {sha[:8]})" if isinstance(sha, str) and sha else ""
    scope = plugin_ref.extra.get("scope")
    marker = _finding_marker(findings_by_ref.get(_ref_key(plugin_ref), []), use_color)
    header = f"{plugin_ref.component_identity}{sha_note} [scope={scope}]{marker}"
    root = _TreeNode(label=header)

    plugin_identity = plugin_ref.component_identity or ""
    # Derive `<plugin>` from `claude-plugin/<plugin>[@version]`; we strip it
    # from bundled command/agent/hook leaf labels under this plugin's block
    # so the leaf reads as `<name>` rather than `<plugin>/<name>`.
    parent_plugin = _plugin_name_from_identity(plugin_identity)
    categories = _bundled_categories(all_refs, plugin_identity)
    tier2 = _tier2_summary(all_refs, plugin_identity)

    for label, _ in _TREE_CATEGORIES:
        items = categories.get(label)
        if not items:
            continue
        cat_node = _TreeNode(label=f"{label}/ ({len(items)})")
        for r in sorted(items, key=lambda x: _leaf_label(x, parent_plugin).lower()):
            leaf_marker = _finding_marker(findings_by_ref.get(_ref_key(r), []), use_color)
            cat_node.children.append(
                _TreeNode(label=f"{_leaf_label(r, parent_plugin)}{leaf_marker}")
            )
        root.children.append(cat_node)

    for eco, kind, count, source, ecorefs in tier2:
        eco_ids = [id for r in ecorefs for id in findings_by_ref.get(_ref_key(r), [])]
        eco_marker = _finding_marker(eco_ids, use_color)
        root.children.append(
            _TreeNode(label=f"{eco}/ deps ({count} {kind} via {source}){eco_marker}")
        )

    if not root.children:
        root.children.append(_TreeNode(label="(no bundled components)"))
    return root


def _build_bare_node(
    refs: list[ComponentRef],
    findings_by_ref: dict[tuple, list[str]],
    use_color: bool,
) -> _TreeNode | None:
    cats = _bare_categories(refs)
    if not cats:
        return None
    root = _TreeNode(label="bare components/")
    for label, _ in _TREE_CATEGORIES:
        items = cats.get(label)
        if not items:
            continue
        cat = _TreeNode(label=f"{label}/ ({len(items)})")
        for r in sorted(items, key=lambda x: _leaf_label(x).lower()):
            leaf_marker = _finding_marker(findings_by_ref.get(_ref_key(r), []), use_color)
            cat.children.append(_TreeNode(label=f"{_leaf_label(r)}{leaf_marker}"))
        root.children.append(cat)
    return root


def _format_tree_lines(node: _TreeNode, chars: dict) -> list[str]:
    """Render a root node and its descendants as box-drawing tree lines."""
    out = [node.label]
    last_idx = len(node.children) - 1
    for i, child in enumerate(node.children):
        _format_subtree(child, "", i == last_idx, chars, out)
    return out


def _format_subtree(
    node: _TreeNode, prefix: str, is_last: bool, chars: dict, out: list[str]
) -> None:
    connector = chars["last"] if is_last else chars["branch"]
    out.append(prefix + connector + node.label)
    next_prefix = prefix + (chars["space"] if is_last else chars["vert"])
    last_idx = len(node.children) - 1
    for i, child in enumerate(node.children):
        _format_subtree(child, next_prefix, i == last_idx, chars, out)


def render_inventory_tree(
    refs: list[ComponentRef],
    findings: list[Finding],
    *,
    use_color: bool = False,
    use_unicode: bool = True,
) -> str:
    """Render the active-plugin and bare-component inventory as a tree.

    One root block per plugin; each shows its bundled components organized by
    category (MCPs/skills/hooks/commands/agents) plus a Tier-2 deps summary
    line per ecosystem. Plugins with zero bundled components emit
    `└── (no bundled components)` rather than an empty subtree, so the user
    can see the plugin was resolved even if it ships nothing of its own.

    Bare components (no plugin attribution) render as a final root block.

    Components that matched a finding carry a `[! <id1>, <id2>]` suffix in red
    (when `use_color=True`); plugin headers are similarly marked when a
    `claude-plugin` advisory fired against the plugin itself.

    `use_unicode=False` swaps box-drawing characters for ASCII (`|--`, `\\`--`),
    useful on terminals or CI logs that mangle UTF-8.
    """
    chars = _TREE_UNICODE if use_unicode else _TREE_ASCII
    findings_by_ref = _findings_by_ref(findings)

    plugins = sorted(
        (r for r in refs if r.ecosystem == "claude-plugin"),
        key=lambda r: (r.component_identity or "").lower(),
    )
    bare_node = _build_bare_node(refs, findings_by_ref, use_color)
    n_plugins = len(plugins)
    n_bare = sum(len(v) for v in _bare_categories(refs).values())
    # Total components = everything minus the plugin self-identity refs.
    n_total = sum(1 for r in refs if r.ecosystem != "claude-plugin")

    out: list[str] = []
    out.append(
        f"{_pluralize(n_plugins, 'active plugin')}, "
        f"{_pluralize(n_bare, 'bare component')}, "
        f"{_pluralize(n_total, 'total component')}"
    )
    out.append("")
    for plugin_ref in plugins:
        node = _build_plugin_node(plugin_ref, refs, findings_by_ref, use_color)
        out.extend(_format_tree_lines(node, chars))
        out.append("")
    if bare_node is not None:
        out.extend(_format_tree_lines(bare_node, chars))
        out.append("")
    return "\n".join(out).rstrip()


def render_repo_inventory_tree(
    root: Path,
    grouped: list[tuple[Path, list[ComponentRef]]],
    findings: list[Finding],
    *,
    use_color: bool = False,
    use_unicode: bool = True,
) -> str:
    """Render repo-mode inventory as a composition tree.

    Repo mode has no endpoint install state, so the tree is derived from
    manifest co-location. A `<dir>/.claude-plugin/plugin.json` ref creates a
    plugin root; agent-dependency refs declared by manifests in `<dir>` render
    below that plugin, while bare agent components render under a final
    `bare components/` block.
    """
    chars = _TREE_UNICODE if use_unicode else _TREE_ASCII
    findings_by_ref = _findings_by_ref(findings)
    all_refs = [r for _, refs in grouped for r in refs]
    plugin_refs = sorted(
        (r for r in all_refs if r.ecosystem == "claude-plugin"),
        key=lambda r: (r.component_identity or "").lower(),
    )

    root_node = _TreeNode(label=f"repo {root}")
    assigned_keys: set[tuple] = set()
    for plugin_ref in plugin_refs:
        plugin_dir = _repo_plugin_dir(plugin_ref)
        node, assigned = _build_repo_plugin_node(
            plugin_ref,
            plugin_dir,
            all_refs,
            findings_by_ref,
            use_color,
        )
        assigned_keys.update(assigned)
        root_node.children.append(node)

    bare_refs = [
        r
        for r in all_refs
        if r.ecosystem != "claude-plugin"
        and r.scope == "agent-component"
        and _ref_key(r) not in assigned_keys
    ]
    bare_node = _build_bare_node(bare_refs, findings_by_ref, use_color)
    if bare_node is not None:
        root_node.children.append(bare_node)

    suppressed = sum(1 for r in all_refs if r.scope == "software-dependency")
    if suppressed:
        root_node.children.append(_TreeNode(label=f"software deps suppressed/ ({suppressed})"))

    if not root_node.children:
        if grouped:
            scanned = _TreeNode(label=f"manifests scanned/ ({len(grouped)})")
            for path, _ in sorted(grouped, key=lambda x: str(x[0])):
                scanned.children.append(_TreeNode(label=_repo_rel(path, root)))
            root_node.children.append(scanned)
        else:
            root_node.children.append(_TreeNode(label="(no agent components)"))
    return "\n".join(_format_tree_lines(root_node, chars))


def _repo_plugin_dir(plugin_ref: ComponentRef) -> Path:
    manifest = Path(plugin_ref.source_manifest)
    if manifest.name == "plugin.json" and manifest.parent.name == ".claude-plugin":
        return manifest.parent.parent
    return manifest.parent


def _build_repo_plugin_node(
    plugin_ref: ComponentRef,
    plugin_dir: Path,
    all_refs: list[ComponentRef],
    findings_by_ref: dict[tuple, list[str]],
    use_color: bool,
) -> tuple[_TreeNode, set[tuple]]:
    marker = _finding_marker(findings_by_ref.get(_ref_key(plugin_ref), []), use_color)
    root = _TreeNode(label=f"{plugin_ref.component_identity}{marker}")
    assigned: set[tuple] = set()

    deps: list[ComponentRef] = []
    categories: dict[str, list[ComponentRef]] = {label: [] for label, _ in _TREE_CATEGORIES}
    for ref in all_refs:
        if ref is plugin_ref or ref.ecosystem == "claude-plugin":
            continue
        if not _repo_ref_in_dir(ref, plugin_dir):
            continue
        if ref.scope == "agent-dependency":
            deps.append(ref)
            assigned.add(_ref_key(ref))
            continue
        if ref.scope != "agent-component":
            continue
        for label, ecos in _TREE_CATEGORIES:
            if ref.ecosystem in ecos:
                categories[label].append(ref)
                assigned.add(_ref_key(ref))
                break

    if deps:
        dep_node = _TreeNode(label=f"package deps/ ({len(deps)})")
        for ref in sorted(deps, key=lambda x: _leaf_label(x).lower()):
            marker = _finding_marker(findings_by_ref.get(_ref_key(ref), []), use_color)
            dep_node.children.append(_TreeNode(label=f"{_leaf_label(ref)}{marker}"))
        root.children.append(dep_node)

    for label, _ in _TREE_CATEGORIES:
        items = categories.get(label) or []
        if not items:
            continue
        cat = _TreeNode(label=f"{label}/ ({len(items)})")
        for ref in sorted(items, key=lambda x: _leaf_label(x).lower()):
            marker = _finding_marker(findings_by_ref.get(_ref_key(ref), []), use_color)
            cat.children.append(_TreeNode(label=f"{_leaf_label(ref)}{marker}"))
        root.children.append(cat)

    if not root.children:
        root.children.append(_TreeNode(label="(no declared components)"))
    return root, assigned


def _repo_ref_in_dir(ref: ComponentRef, directory: Path) -> bool:
    if not ref.source_manifest:
        return False
    try:
        return Path(ref.source_manifest).resolve().parent == directory.resolve()
    except OSError:
        return False


def _repo_rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
