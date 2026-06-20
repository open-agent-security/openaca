"""End-to-end OpenACA scan: parse → OSV match → overlay → report.

Two modes via subcommands (per ADR-0006); a subcommand is required:

    openaca scan repo --target <repo> [...]
        Walks supported agent-component manifests committed in the target
        repository. Covers (a) project-host config under `.claude/*`
        (which describes what Claude Code loads when run in this repo,
        i.e. developer-agent posture committed to source), and
        (b) manifest-backed SDK config like a root `.mcp.json` an app
        loads via `query({ options: { mcpConfig: "..." } })`. Does NOT
        cover SDK-inline definitions, code-registered tools, or anything
        requiring source-code extraction — those are V1. Treat repo
        findings as *declared* composition, not deployed-app
        composition.

    openaca scan endpoint [--config-dir <claude-config-dir>] [--project <repo>]
        Install-state-aware endpoint scan: reads settings.json +
        installed_plugins.json to enumerate the active agent composition.
        Defaults to $CLAUDE_CONFIG_DIR, else ~/.claude. --project layers
        project/local settings when scanning a repo's endpoint context.

Common options (--sarif, --fail-on, -v) can be placed before or after the
subcommand name; the group forwards them either way:

    openaca scan -v repo --target X
    openaca scan repo --target X -v   # equivalent

Findings carry an optional `attributed_to` field (e.g.,
"plugin/<marketplace>/<name>@<version>") set by parsers when a component was
discovered via an active plugin. Output prefixes the finding with `via <X>`
when present; SARIF surfaces it in `properties.attributed_to`.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import click
from click.core import ParameterSource

from tools.bom import (
    build_agent_bom,
    component_refs_from_cyclonedx,
    source_unit_from_cyclonedx,
    target_info_from_cyclonedx,
)
from tools.component_ref import ComponentRef
from tools.graph import Graph
from tools.graph_build import build_graph
from tools.matcher import Finding, match
from tools.observations import (
    ObservationFinding,
    collect_skill_observations,
    collect_skillspector_findings,
)
from tools.osv_federation import augment_corpus, collect_osv_query_labels, is_queryable
from tools.overlays import apply_overlays, build_alias_to_overlay_id_map, load_overlays
from tools.parsers import parse_repo_grouped
from tools.posture import (
    PostureFinding,
    collect_endpoint_mcp_manifests,
    collect_endpoint_settings_manifests,
    collect_mcp_manifests,
    collect_settings_manifests,
    run_posture_rules,
)
from tools.render import (
    RenderTarget,
    ScanStats,
    render_github,
    render_inventory_tree,
    render_json,
    render_repo_inventory_tree,
    render_text,
)
from tools.sarif import to_sarif

_FORMAT_CHOICES = ("text", "github", "json")

# Internal ref classifications that are surfaced to users in V0. Everything
# else (software-dependency) is suppressed from matching, federation, and
# rendering — OpenACA V0 is agent-composition analysis.
_AGENT_SCOPES: frozenset[str] = frozenset({"agent-component", "agent-dependency"})


def _filter_agent_scope_refs(refs: list[ComponentRef]) -> list[ComponentRef]:
    """Drop software-dependency refs before they reach matching/federation/rendering."""
    return [r for r in refs if r.scope in _AGENT_SCOPES]


def _refs_from_graph(graph: Graph) -> list[ComponentRef]:
    """Project the graph's non-root nodes into the flat ref list scan consumes.

    The graph is the single source of truth (Stage 3): `scope` and
    `attributed_to` are derived from graph structure — `scope_of` (agent- vs
    software-dependency from the lineage) and `nearest_plugin_ancestor`
    (reproducing the old "via plugin" semantics: nearest plugin identity, or
    None). `ComponentRef` is frozen, so use `dataclasses.replace` to stamp the
    derived values rather than mutating in place. Downstream consumers
    (BOM/render/matcher) keep working unchanged off these stamped refs;
    later stages migrate them to read the graph directly.
    """
    refs: list[ComponentRef] = []
    for node in graph.nodes.values():
        if node.ref is None:  # the synthetic target root has no ref
            continue
        refs.append(
            replace(
                node.ref,
                scope=graph.scope_of(node),
                attributed_to=_attribution_for(graph, node),
            )
        )
    return refs


def _attribution_for(graph: Graph, node) -> str | None:
    """The nearest plugin ancestor's attribution string, or None.

    Reproduces the pre-graph `attributed_to` value exactly: the plugin's
    component_identity, versioned (`<identity>@<version>`) when the plugin
    carries a version — matching `claude_plugin.parse` and `claude_install`'s
    `attributed_id`. A component is its own plugin only via ancestry, so a
    plugin node itself attributes to None (no plugin ancestor)."""
    plugin = graph.nearest_plugin_ancestor(node)
    if plugin is None or plugin.ref is None:
        return None
    identity = plugin.ref.component_identity
    if not identity:
        return None
    return f"{identity}@{plugin.ref.version}" if plugin.ref.version else identity


def default_overlays_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "overlays"


def _component_type(ref: ComponentRef) -> str:
    value = (ref.extra or {}).get("component_type")
    return value if isinstance(value, str) and value else "component"


def _is_plugin_ref(ref: ComponentRef) -> bool:
    return _component_type(ref) == "plugin" and bool(
        ref.component_identity and ref.component_identity.startswith("plugin/")
    )


def _collect_scanner_findings(
    refs: list[ComponentRef],
    *,
    external_scanners: tuple[str, ...],
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    observations = collect_skill_observations(refs)
    posture_findings: list[PostureFinding] = []
    if "nvidia-skillspector" in external_scanners:
        skillspector_findings = collect_skillspector_findings(refs)
        observations.extend(skillspector_findings.observations)
        posture_findings.extend(skillspector_findings.posture_findings)
        for warning in skillspector_findings.warnings:
            click.echo(f"warning: {warning}", err=True)
    return observations, posture_findings


def _component_label(ref: ComponentRef) -> str:
    """Human-readable identifier for a component, preferring PURL form."""
    purl = ref.purl
    if purl:
        return purl
    if ref.component_identity:
        return ref.component_identity
    if ref.ecosystem and ref.name:
        if ref.version:
            return f"{ref.ecosystem}:{ref.name}@{ref.version}"
        return f"{ref.ecosystem}:{ref.name}"
    return "<unidentified>"


def _finding_line(f: Finding) -> str:
    """Render a finding line for verbose output, including attribution suffix."""
    base = f"{_component_label(f.component)} → {f.advisory_id} ({f.confidence})"
    if f.attributed_to:
        return f"{base} via {f.attributed_to}"
    return base


def _federation_targets_lines(refs: list[ComponentRef], fetched_count: int) -> list[str]:
    """Render the verbose OSV.dev federation summary.

    Three parts: fetched record count, queried target list (what was actually
    sent), and skipped refs bucketed by source ecosystem or component type.
    Source-less agent components have no supported OSV query shape.
    """
    queried = collect_osv_query_labels(refs)
    lines: list[str] = []
    if queried:
        lines.append(
            f"federation: queried {len(queried)} target(s) on osv.dev; "
            f"fetched {fetched_count} advisory record(s)"
        )
        for target in queried:
            lines.append(f"  {target}")
    else:
        lines.append("federation: no queryable OSV.dev targets")
    skipped_by_eco: dict[str, int] = {}
    for r in refs:
        if is_queryable(r):
            continue
        eco = r.ecosystem or _component_type(r)
        skipped_by_eco[eco] = skipped_by_eco.get(eco, 0) + 1
    if skipped_by_eco:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(skipped_by_eco.items()))
        total = sum(skipped_by_eco.values())
        lines.append(
            f"federation: skipped {total} ref(s) without supported OSV.dev query ({parts})"
        )
    return lines


def _stamp_source(corpus: list[dict], source: str) -> None:
    """Set `database_specific.openaca.source = <source>` on every advisory
    that doesn't already declare a source. Mutates corpus in place."""
    for a in corpus:
        if not isinstance(a, dict):
            continue
        ds = a.setdefault("database_specific", {})
        if not isinstance(ds, dict):
            continue
        openaca_block = ds.setdefault("openaca", {})
        if isinstance(openaca_block, dict) and "source" not in openaca_block:
            openaca_block["source"] = source


def _exit_for_findings(fail_on: str, findings: list[Finding]) -> None:
    if not findings:
        sys.exit(0)
    if fail_on == "none":
        sys.exit(0)
    high_count = sum(1 for f in findings if f.confidence == "high")
    if fail_on == "high" and high_count == 0:
        sys.exit(0)
    sys.exit(1)


# Subcommand option decorators.
_target_option_required = click.option(
    "--target",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Path to scan.",
)
_config_dir_option = click.option(
    "--config-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Agent host config directory. Defaults to $CLAUDE_CONFIG_DIR, else ~/.claude.",
)
_project_option = click.option(
    "--project",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help=(
        "Project root whose .claude settings/skills/MCPs are layered into endpoint "
        "resolution. Pass `--project .` to include the current directory's project "
        "context. Endpoint scan does NOT include project context by default — when "
        "this flag is omitted, scan output reminds you how to add it."
    ),
)
_sarif_option = click.option(
    "--sarif",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write SARIF v2.1.0 to this path.",
)
_fail_on_option = click.option(
    "--fail-on",
    type=click.Choice(["high", "any", "none"]),
    default="any",
    show_default=True,
    help="Exit non-zero when findings of this severity are present.",
)
_verbose_option = click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Print the per-manifest component breakdown and matched components.",
)
_format_option = click.option(
    "--format",
    "output_format",
    type=click.Choice(_FORMAT_CHOICES),
    default="text",
    show_default=True,
    help=(
        "Output format. `text` (default) is grouped human-readable. `github` "
        "emits workflow annotation lines (auto-enabled when GITHUB_ACTIONS=true). "
        "`json` emits a structured document for tool consumption."
    ),
)
_no_color_option = click.option(
    "--no-color",
    is_flag=True,
    default=False,
    help="Disable ANSI colors in text output. Colors are also off when stdout is not a TTY.",
)
_include_posture_option = click.option(
    "--include-posture",
    is_flag=True,
    default=False,
    help=(
        "Also emit scanner-side posture findings (configuration hygiene rules: "
        "mutable install refs, insecure transport, endpoint overrides, MCP auto-approval, "
        "and posture claims from enabled external scanners). Posture findings are distinct "
        "from vulnerability findings and never affect --fail-on exit codes."
    ),
)
_scanner_option = click.option(
    "--scanner",
    "external_scanners",
    type=click.Choice(["nvidia-skillspector"]),
    multiple=True,
    help=("Run an optional external scanner. OpenACA analysis always runs. May be repeated."),
)
_bom_input_option = click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="CycloneDX Agent BOM JSON to scan.",
)


# Group-level shared options (sarif / fail-on / verbose) can be placed BEFORE
# the subcommand name as a convenience; `_apply_group_opts` forwards them to
# the chosen subcommand. A subcommand is required — there is no
# no-subcommand fallback.
@click.group()
@click.pass_context
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_include_posture_option
def main(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    include_posture: bool,
) -> None:
    """OpenACA scanner. Use `repo` or `endpoint` subcommands."""
    ctx.ensure_object(dict)
    ctx.obj["sarif"] = sarif
    ctx.obj["fail_on"] = fail_on
    ctx.obj["verbose"] = verbose
    # Track whether each option was explicitly set at the group level; the
    # subcommand may inherit the explicit value over its own default.
    ctx.obj["format"] = output_format
    ctx.obj["format_explicit"] = (
        ctx.get_parameter_source("output_format") != ParameterSource.DEFAULT
    )
    ctx.obj["no_color"] = no_color
    ctx.obj["include_posture"] = include_posture


def _apply_group_opts(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    include_posture: bool,
) -> tuple[Path | None, str, bool, str, bool, bool]:
    """Forward shared options placed before the subcommand name.

    When a user runs `openaca scan --fail-on none repo ...`, Click parses
    --fail-on at the group level and the subcommand sees its own default.
    Read the group's ctx.obj and apply any option the subcommand didn't
    explicitly receive from the command line.

    `output_format` also auto-promotes to `github` when GITHUB_ACTIONS=true
    and the user didn't pass `--format` explicitly at either level.
    """
    obj = (ctx.parent.obj if ctx.parent else None) or {}
    if ctx.get_parameter_source("sarif") == ParameterSource.DEFAULT:
        sarif = obj.get("sarif", sarif)
    if ctx.get_parameter_source("fail_on") == ParameterSource.DEFAULT:
        fail_on = obj.get("fail_on", fail_on)
    if ctx.get_parameter_source("verbose") == ParameterSource.DEFAULT:
        verbose = obj.get("verbose", verbose)

    sub_format_explicit = ctx.get_parameter_source("output_format") != ParameterSource.DEFAULT
    if not sub_format_explicit:
        if obj.get("format_explicit"):
            output_format = obj.get("format", output_format)
        elif os.environ.get("GITHUB_ACTIONS") == "true":
            output_format = "github"

    if ctx.get_parameter_source("no_color") == ParameterSource.DEFAULT:
        no_color = obj.get("no_color", no_color)
    if ctx.get_parameter_source("include_posture") == ParameterSource.DEFAULT:
        include_posture = obj.get("include_posture", include_posture)
    return sarif, fail_on, verbose, output_format, no_color, include_posture


def _use_color(no_color: bool, output_format: str) -> bool:
    """Color is on for `text` only, when stdout is a TTY, and not opted out."""
    if no_color or output_format != "text":
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, OSError):
        return False


def _use_unicode(no_color: bool) -> bool:
    """Use Unicode box-drawing for the inventory tree when the locale supports
    UTF-8. Falls back to ASCII when `--no-color` is set or the encoding looks
    non-UTF-8 — CI logs and minimal terminals get a clean parseable rendering."""
    if no_color:
        return False
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


def _emit(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    stats: ScanStats,
    *,
    output_format: str,
    use_color: bool,
    verbose: bool,
    posture_findings: list[PostureFinding] | None = None,
    observations: list[ObservationFinding] | None = None,
    target: RenderTarget | None = None,
    inventory_tree: str | None = None,
    next_actions: list[str] | None = None,
    graph: Graph | None = None,
) -> None:
    """Dispatch to the chosen renderer and write to stdout.

    `target`/`inventory_tree`/`next_actions` drive the text card and are ignored
    by the machine formats (github/json), whose stdout shape is unchanged.
    """
    if output_format == "github":
        rendered = render_github(
            findings, posture_findings=posture_findings, observations=observations
        )
    elif output_format == "json":
        rendered = render_json(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_findings,
            observations=observations,
        )
    else:
        rendered = render_text(
            findings,
            advisory_index,
            stats,
            use_color=use_color,
            verbose=verbose,
            posture_findings=posture_findings,
            observations=observations,
            target=target,
            inventory_tree=inventory_tree,
            next_actions=next_actions,
        )
    if rendered:
        click.echo(rendered)


def _render_bom_inventory_tree(
    refs: list[ComponentRef],
    findings: list[Finding],
    *,
    target_type: str | None,
    target: str | None,
    input_path: Path,
    use_color: bool,
    use_unicode: bool,
) -> str:
    if target_type == "repo":
        root = Path(target) if target else input_path.parent
        grouped = _group_refs_for_repo_tree(refs)
        return render_repo_inventory_tree(
            root, grouped, findings, use_color=use_color, use_unicode=use_unicode
        )
    return render_inventory_tree(refs, findings, use_color=use_color, use_unicode=use_unicode)


def _group_refs_for_repo_tree(refs: list[ComponentRef]) -> list[tuple[Path, list[ComponentRef]]]:
    grouped: dict[str, list[ComponentRef]] = {}
    for ref in refs:
        key = ref.source_manifest or ""
        grouped.setdefault(key, []).append(ref)
    return [(Path(path), refs) for path, refs in grouped.items()]


def _collect_corpus_sources(corpus: list[dict]) -> set[str]:
    """Pull `database_specific.openaca.source` from every advisory in the corpus."""
    sources: set[str] = set()
    for a in corpus:
        if not isinstance(a, dict):
            continue
        ds = a.get("database_specific")
        if not isinstance(ds, dict):
            continue
        openaca = ds.get("openaca")
        if not isinstance(openaca, dict):
            continue
        src = openaca.get("source")
        if isinstance(src, str) and src:
            sources.add(src)
    return sources


def _load_osv_with_overlays(
    refs: list[ComponentRef],
) -> tuple[list[dict], list[str], int, dict[str, str]]:
    """Query OSV for refs and merge OpenACA overlays into returned records."""
    overlays = load_overlays(default_overlays_dir())
    corpus, warnings = augment_corpus(refs, [])
    alias_map = build_alias_to_overlay_id_map(overlays)
    return apply_overlays(corpus, overlays), warnings, len(overlays), alias_map


def _stderr_summary(
    findings: list[Finding],
    summary_prefix: str,
    output_format: str,
) -> None:
    """For non-text formats only: emit the existing one-line stderr summary
    so machine consumers (CI parsers, json pipelines) still see the totals.
    The text renderer's own footer covers this for terminal users."""
    if output_format == "text":
        return
    if not findings:
        click.echo(f"{summary_prefix}; no findings", err=True)
        return
    high_count = sum(1 for f in findings if f.confidence == "high")
    click.echo(
        f"{summary_prefix}; {len(findings)} finding(s), {high_count} high-confidence",
        err=True,
    )


@main.command()
@click.pass_context
@_target_option_required
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_include_posture_option
@_scanner_option
@click.option(
    "--include-gitignored",
    is_flag=True,
    default=False,
    help=(
        "Walk paths matched by <target>/.gitignore. Default skips them to avoid "
        "noisy findings from node_modules/, .venv/, dist/, and other build "
        "artifacts. .git/ is always skipped."
    ),
)
def repo(
    ctx: click.Context,
    target: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    include_posture: bool,
    external_scanners: tuple[str, ...],
    include_gitignored: bool,
) -> None:
    """Scan supported agent-component manifests committed in a repository.

    Reports declared composition only: project-host config under
    `.claude/*` (what Claude Code would load if run in this repo) and
    manifest-backed SDK config like a root `.mcp.json`. SDK-inline and
    code-defined agent composition (e.g., `Agent(tools=[...])`,
    `query({ mcpServers: ... })`) are out of V0 scope and not surfaced.
    """
    sarif, fail_on, verbose, output_format, no_color, include_posture = _apply_group_opts(
        ctx, sarif, fail_on, verbose, output_format, no_color, include_posture
    )

    # The composition graph is the single source of truth (Stage 3): scope and
    # attribution are derived from graph structure, not path heuristics.
    graph = build_graph(target, mode="repo", include_gitignored=include_gitignored)
    all_refs = _refs_from_graph(graph)
    # Reconstruct the per-manifest `grouped` list the repo renderer expects by
    # grouping the projected refs by their source_manifest Path; the renderer is
    # unchanged and reads graph-derived scope/attribution off each ref.
    grouped = _group_refs_for_repo_tree(all_refs)
    # Manifest-visited count and parse-failure reporting are properties of the
    # filesystem walk, not the graph; source them from the walk so the scanned/
    # failed-to-parse summary is unchanged. (No scope/attribution comes from
    # here — that is graph-derived.)
    parse_groups, n_found = parse_repo_grouped(target, include_gitignored=include_gitignored)
    n_failed = n_found - len(parse_groups)
    # V0: drop software-dependency refs (deps from non-plugin manifests).
    # OpenACA is agent-composition analysis; deps belonging to general
    # software in the repo are out of scope and would mislead users into
    # thinking OpenACA is a general SCA tool. See README for framing.
    refs = build_agent_bom(
        _filter_agent_scope_refs(all_refs),
        target_type="repo",
        target=str(target),
        source_unit_count=n_found,
        source_unit_label="manifest",
        graph=graph,
    ).component_refs()
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(refs)
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus, graph=graph)
    observations, scanner_posture_findings = _collect_scanner_findings(
        refs, external_scanners=external_scanners
    )

    posture_findings: list[PostureFinding] = []
    if include_posture:
        posture_findings.extend(scanner_posture_findings)
        manifests = collect_mcp_manifests([target], include_gitignored=include_gitignored)
        settings_manifests = collect_settings_manifests(
            [target], include_gitignored=include_gitignored
        )
        posture_findings.extend(run_posture_rules(refs, manifests, settings_manifests))

    # None means posture was not requested (rendered as "skipped"); [] means it ran and
    # found nothing. Don't collapse the empty-but-ran case to None.
    posture_output = posture_findings if include_posture else None

    advisory_index = {a["id"]: a for a in corpus}
    parse_note = f" ({n_failed} failed to parse)" if n_failed else ""

    # Build the inventory tree for the text card (default stdout). For machine
    # formats the tree stays a verbose-stderr-only diagnostic (below), since
    # their stdout is consumed by tooling.
    is_text = output_format == "text"
    card_tree: str | None = None
    if is_text and grouped:
        card_tree = render_repo_inventory_tree(
            target,
            grouped,
            findings,
            use_color=_use_color(no_color, output_format),
            use_unicode=_use_unicode(no_color),
            graph=graph,
        )
    card_target = RenderTarget(host_surface="repository", rows=[("path", str(target))])
    card_next = [
        f"emit Agent BOM: openaca bom repo --target {target} --output openaca-bom.json",
    ]

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        if grouped:
            # For text, the tree is in the stdout card; don't duplicate on stderr.
            if not is_text:
                click.echo(
                    f"scanned {n_found} manifest(s), {len(refs)} component(s){parse_note}:",
                    err=True,
                )
                tree = render_repo_inventory_tree(
                    target,
                    grouped,
                    findings,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=graph,
                )
                if tree:
                    click.echo(tree, err=True)
        elif n_found:
            click.echo(f"found {n_found} manifest file(s) but none parsed successfully", err=True)
        else:
            click.echo(f"no manifests found under {target}", err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=n_found,
        unit_label="manifest",
        component_count=len(refs),
        parse_failed=n_failed,
        sources=_collect_corpus_sources(corpus),
    )
    _emit(
        findings,
        advisory_index,
        stats,
        output_format=output_format,
        use_color=_use_color(no_color, output_format),
        verbose=verbose,
        posture_findings=posture_output,
        observations=observations,
        target=card_target,
        inventory_tree=card_tree,
        next_actions=card_next,
        graph=graph,
    )

    # For machine formats (github, json), keep the existing one-line stderr
    # summary so consumers parsing only stdout still get totals on stderr.
    # text format's footer already includes them.
    if not grouped and output_format != "text":
        if n_found:
            click.echo(
                f"found {n_found} manifest file(s) but none parsed successfully",
                err=True,
            )
        else:
            click.echo(f"no manifests found under {target}", err=True)
    else:
        _stderr_summary(
            findings,
            f"scanned {n_found} manifest(s), {len(refs)} component(s){parse_note}",
            output_format,
        )

    _exit_for_findings(fail_on, findings)


@main.command()
@click.pass_context
@_config_dir_option
@_project_option
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_include_posture_option
@_scanner_option
def endpoint(
    ctx: click.Context,
    config_dir: Path | None,
    project: Path | None,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    include_posture: bool,
    external_scanners: tuple[str, ...],
) -> None:
    """Scan the active agent composition installed on this endpoint."""
    sarif, fail_on, verbose, output_format, no_color, include_posture = _apply_group_opts(
        ctx, sarif, fail_on, verbose, output_format, no_color, include_posture
    )
    config_dir = _resolve_endpoint_config_dir(config_dir)

    # Scan-scope transparency. For the default text card the Target block owns
    # this, so the stderr preamble would just precede (and duplicate) the card;
    # emit it only for machine formats or verbose runs.
    is_text = output_format == "text"
    project_note = str(project) if project is not None else "(none)"
    if not is_text or verbose:
        click.echo(
            f"detected config_dir={config_dir}, project={project_note} (mode=endpoint)",
            err=True,
        )

    graph = build_graph(config_dir, mode="endpoint", project_root=project)
    refs = _refs_from_graph(graph)
    warnings: list[str] = []
    refs = build_agent_bom(
        _filter_agent_scope_refs(refs),
        target_type="endpoint",
        target=str(config_dir),
        source_unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
        source_unit_label="active plugin",
        graph=graph,
    ).component_refs()
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(refs)
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus, graph=graph)
    observations, scanner_posture_findings = _collect_scanner_findings(
        refs, external_scanners=external_scanners
    )

    posture_findings: list[PostureFinding] = []
    if include_posture:
        posture_findings.extend(scanner_posture_findings)
        manifests = collect_endpoint_mcp_manifests(config_dir, project, refs)
        settings_manifests = collect_endpoint_settings_manifests(config_dir, project)
        posture_findings.extend(run_posture_rules(refs, manifests, settings_manifests))

    # None means posture was not requested (rendered as "skipped"); [] means it ran and
    # found nothing. Don't collapse the empty-but-ran case to None.
    posture_output = posture_findings if include_posture else None

    advisory_index = {a["id"]: a for a in corpus}
    plugin_count = sum(1 for r in refs if _is_plugin_ref(r))

    # Inventory tree for the text card (default stdout). Machine formats keep the
    # tree as a verbose-stderr diagnostic only (below).
    card_target = RenderTarget(
        host_surface="Claude Code",
        rows=[
            ("config", str(config_dir)),
            ("project", str(project) if project is not None else "not included"),
        ],
    )
    card_tree: str | None = None
    if is_text:
        card_tree = render_inventory_tree(
            refs,
            findings,
            use_color=_use_color(no_color, output_format),
            use_unicode=_use_unicode(no_color),
            graph=graph,
        )
    card_next: list[str] = []
    if project is None:
        card_next.append("include project-local config: openaca scan endpoint --project .")
    card_next.append("emit Agent BOM: openaca bom endpoint --output openaca-bom.json")
    card_next.append("sync to remote: openaca remote sync endpoint")

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        for w in warnings:
            click.echo(f"  warning: {w}", err=True)
        # For text, the tree is in the stdout card; don't duplicate on stderr.
        if not is_text:
            tree = render_inventory_tree(
                refs,
                findings,
                use_color=_use_color(no_color, output_format),
                use_unicode=_use_unicode(no_color),
                graph=graph,
            )
            if tree:
                click.echo(tree, err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=plugin_count,
        unit_label="active plugin",
        component_count=len(refs),
        sources=_collect_corpus_sources(corpus),
    )
    _emit(
        findings,
        advisory_index,
        stats,
        output_format=output_format,
        use_color=_use_color(no_color, output_format),
        verbose=verbose,
        posture_findings=posture_output,
        observations=observations,
        target=card_target,
        inventory_tree=card_tree,
        next_actions=card_next,
        graph=graph,
    )
    _stderr_summary(findings, f"resolved {plugin_count} active plugin(s)", output_format)

    # When --project is not provided, remind the user that project-local
    # skills/MCPs/plugin manifests are NOT included in this scan. For the text
    # card this lives in the Next block, so only emit the stderr note for
    # machine formats or verbose runs (avoids duplicating it for text users).
    if project is None and (not is_text or verbose):
        click.echo(
            "\nNote: scanned user-level config only. To include project-local "
            "skills, MCPs, and plugin manifests, pass --project /path/to/project "
            "(or --project . for the current directory).",
            err=True,
        )

    _exit_for_findings(fail_on, findings)


@main.command(name="bom")
@click.pass_context
@_bom_input_option
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
def scan_bom(
    ctx: click.Context,
    input_path: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
) -> None:
    """Scan a previously generated Agent BOM.

    BOM scans perform advisory matching against composition captured in the
    BOM. Posture findings are not replayed because those rules require the
    original local configuration files, not just the composition snapshot.
    """
    sarif, fail_on, verbose, output_format, no_color, include_posture = _apply_group_opts(
        ctx,
        sarif,
        fail_on,
        verbose,
        output_format,
        no_color,
        include_posture=False,
    )
    group_opts = (ctx.parent.obj if ctx.parent else None) or {}
    if include_posture or group_opts.get("include_posture"):
        raise click.ClickException(
            "--include-posture is not supported for scan bom; posture checks "
            "require the original repo or endpoint configuration."
        )
    try:
        doc = json.loads(input_path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise click.ClickException(f"{input_path}: not valid UTF-8 — {exc}") from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"{input_path}: invalid JSON — {exc}") from exc
    if not isinstance(doc, dict):
        raise click.ClickException(
            f"{input_path}: BOM must be a JSON object, got {type(doc).__name__}"
        )
    target_type, target = target_info_from_cyclonedx(doc)
    source_unit_count, source_unit_label = source_unit_from_cyclonedx(doc)
    refs = build_agent_bom(
        _filter_agent_scope_refs(component_refs_from_cyclonedx(doc)),
        target_type="bom",
        target=str(input_path),
    ).component_refs()
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(refs)
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)
    observations = []
    advisory_index = {a["id"]: a for a in corpus}

    # Inventory tree for the text card; machine formats keep it verbose-only.
    is_text = output_format == "text"
    bom_rows: list[tuple[str, str]] = [("file", str(input_path))]
    if target_type:
        orig = f"{target_type} {target}".strip() if target else target_type
        bom_rows.append(("original target", orig))
    card_target = RenderTarget(host_surface="Agent BOM", rows=bom_rows)
    card_tree: str | None = None
    if is_text:
        card_tree = _render_bom_inventory_tree(
            refs,
            findings,
            target_type=target_type,
            target=target,
            input_path=input_path,
            use_color=_use_color(no_color, output_format),
            use_unicode=_use_unicode(no_color),
        )

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        unit_count = source_unit_count if source_unit_count is not None else 1
        unit_label = source_unit_label or "agent BOM"
        # For text, the tree is in the stdout card; don't duplicate on stderr.
        if not is_text:
            click.echo(f"scanned {unit_count} {unit_label}(s), {len(refs)} component(s):", err=True)
            tree = _render_bom_inventory_tree(
                refs,
                findings,
                target_type=target_type,
                target=target,
                input_path=input_path,
                use_color=_use_color(no_color, output_format),
                use_unicode=_use_unicode(no_color),
            )
            if tree:
                click.echo(tree, err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index, overlay_id_map, observations=None)
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=source_unit_count if source_unit_count is not None else 1,
        unit_label=source_unit_label or "agent BOM",
        component_count=len(refs),
        sources=_collect_corpus_sources(corpus),
    )
    _emit(
        findings,
        advisory_index,
        stats,
        output_format=output_format,
        use_color=_use_color(no_color, output_format),
        verbose=verbose,
        observations=observations,
        target=card_target,
        inventory_tree=card_tree,
    )
    unit_count = source_unit_count if source_unit_count is not None else 1
    unit_label = source_unit_label or "agent BOM"
    _stderr_summary(
        findings,
        f"scanned {unit_count} {unit_label}(s), {len(refs)} component(s)",
        output_format,
    )
    _exit_for_findings(fail_on, findings)


def _resolve_endpoint_config_dir(config_dir: Path | None) -> Path:
    """Resolve endpoint config directory defaults.

    Explicit `--config-dir` wins. Otherwise use `$CLAUDE_CONFIG_DIR` when set,
    then fall back to Claude Code's default user config directory.
    """
    if config_dir is not None:
        return config_dir.expanduser()
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".claude"


if __name__ == "__main__":
    main()
