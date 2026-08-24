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

Finding attribution (e.g. "plugin/<marketplace>/<name>@<version>") is derived
at output time from the composition graph — a finding's component is mapped
back to its graph node, then to its nearest plugin ancestor. Output prefixes
the finding with `via <X>` when present; SARIF surfaces it in
`properties.attributed_to`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import click
from click.core import ParameterSource

from tools.agent_kinds import (
    AgentInstance,
    AgentKind,
    DiscoveryContext,
    build_agent_graph,
    discover_agents,
    kind_for,
    resolve_coverage,
)
from tools.bom import (
    AGENT_ROOT_PREFIX,
    AgentInfo,
    agent_info_from_cyclonedx,
    build_agent_bom,
    component_refs_from_cyclonedx,
    graph_from_cyclonedx,
    source_unit_from_cyclonedx,
    target_info_from_cyclonedx,
)
from tools.component_ref import ComponentRef
from tools.finding_output import graph_for
from tools.graph import Graph
from tools.graph_build import _TARGET_KEY
from tools.matcher import Finding, match
from tools.observations import (
    ObservationFinding,
    SkillSpectorCommandNotFound,
    collect_skill_observations,
    collect_skillspector_findings,
)
from tools.osv_federation import augment_corpus, collect_osv_query_labels, is_queryable
from tools.overlays import apply_overlays, build_alias_to_overlay_id_map, load_overlays
from tools.parsers import parse_repo_grouped
from tools.posture import PostureFinding, run_posture_rules
from tools.render import (
    AgentCard,
    AgentSummary,
    RenderTarget,
    ScanStats,
    render_github,
    render_inventory_tree,
    render_json,
    render_repo_inventory_tree,
    render_text,
)
from tools.sarif import to_sarif
from tools.triage import build_triage_cards
from tools.triage_render import TriageFormat, render_triage_report

_FORMAT_CHOICES = ("text", "github", "json", "markdown")

# Internal ref classifications that are surfaced to users in V0. Everything
# else (software-dependency) is suppressed from matching, federation, and
# rendering — OpenACA V0 is agent-composition analysis.
_AGENT_SCOPES: frozenset[str] = frozenset({"agent-component", "agent-dependency"})
OsvProgressCallback = Callable[[str, int, int], None]
SkillSpectorProgressCallback = Callable[[int, int], None]


def _filter_agent_scope_refs(refs: list[ComponentRef]) -> list[ComponentRef]:
    """Drop software-dependency refs before they reach matching/federation/rendering."""
    return [r for r in refs if r.scope in _AGENT_SCOPES]


def _refs_from_graph(graph: Graph) -> list[ComponentRef]:
    """Project the graph's non-root nodes into the flat ref list scan consumes.

    The graph is the single source of truth: `scope` is derived from graph
    structure (`scope_of` — agent- vs software-dependency from the lineage)
    and stamped onto each ref. Attribution ("via plugin X") is no longer a ref
    field; consumers derive it from the graph at output time via
    `attribution_for`. `ComponentRef` is frozen, so use `dataclasses.replace`
    to stamp the derived scope rather than mutating in place.
    """
    refs: list[ComponentRef] = []
    for node in graph.nodes.values():
        if node.ref is None:  # the synthetic target root has no ref
            continue
        refs.append(
            replace(
                node.ref,
                scope=graph.scope_of(node),
                extra={**(node.ref.extra or {}), "bom_ref": node.key},
            )
        )
    return refs


def default_overlays_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "overlays"


def _component_type(ref: ComponentRef) -> str:
    value = (ref.extra or {}).get("component_type")
    return value if isinstance(value, str) and value else "component"


def _is_plugin_ref(ref: ComponentRef) -> bool:
    return _component_type(ref) == "plugin"


def _osv_progress_reporter(output_format: str) -> OsvProgressCallback | None:
    if output_format != "text":
        return None

    def report(stage: str, current: int, total: int) -> None:
        if stage == "query":
            click.echo(f"osv.dev: querying {total} target(s)...", err=True)
            return
        if stage != "fetch":
            return
        if current == 1:
            click.echo(f"osv.dev: fetching {total} advisory record(s)...", err=True)
        if current == total or current % 10 == 0:
            click.echo(f"osv.dev: fetched {current}/{total} advisory record(s)", err=True)

    return report


def _skillspector_progress_reporter(
    output_format: str,
) -> SkillSpectorProgressCallback | None:
    if output_format != "text":
        return None

    def report(current: int, total: int) -> None:
        if current == 1:
            click.echo(f"skillspector: scanning {total} skill(s)...", err=True)
        if current == total or current % 10 == 0:
            click.echo(f"skillspector: scanning skill {current}/{total}", err=True)

    return report


def _collect_scanner_findings(
    refs: list[ComponentRef],
    *,
    external_scanners: tuple[str, ...],
    skillspector_progress: SkillSpectorProgressCallback | None = None,
    agent_kind: str | None = None,
    agent_id: str | None = None,
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    observations = collect_skill_observations(refs)
    posture_findings: list[PostureFinding] = []
    if "nvidia-skillspector" in external_scanners:
        try:
            skillspector_findings = collect_skillspector_findings(
                refs, progress=skillspector_progress, agent_kind=agent_kind
            )
        except SkillSpectorCommandNotFound as exc:
            raise click.ClickException(str(exc)) from exc
        observations.extend(skillspector_findings.observations)
        posture_findings.extend(skillspector_findings.posture_findings)
        for warning in skillspector_findings.warnings:
            click.echo(f"warning: {warning}", err=True)
    if agent_kind is not None:
        observations = [replace(o, agent_kind=agent_kind, agent_id=agent_id) for o in observations]
        posture_findings = [
            replace(p, agent_kind=agent_kind, agent_id=agent_id) for p in posture_findings
        ]
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


def _finding_line(f: Finding, graph: Graph | None = None) -> str:
    """Render a finding line for verbose output, including attribution suffix."""
    base = f"{_component_label(f.component)} → {f.advisory_id} ({f.confidence})"
    attributed_to = graph.attribution_for_ref(f.component) if graph is not None else None
    if attributed_to:
        return f"{base} via {attributed_to}"
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

_report_option = click.option(
    "--report",
    "report_kind",
    type=click.Choice(["exposure"]),
    default=None,
    help="Render a triage report from this scan. Currently supports `exposure`.",
)

_output_option = click.option(
    "-o",
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write --report output to this file instead of stdout.",
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
    *,
    report_kind: str | None = None,
) -> tuple[Path | None, str, bool, str, bool, bool]:
    """Forward shared options placed before the subcommand name.

    When a user runs `openaca scan --fail-on none repo ...`, Click parses
    --fail-on at the group level and the subcommand sees its own default.
    Read the group's ctx.obj and apply any option the subcommand didn't
    explicitly receive from the command line.

    `output_format` also auto-promotes to `github` when GITHUB_ACTIONS=true
    and the user didn't pass `--format` explicitly at either level. That
    promotion is skipped when `report_kind` is set: `--report exposure`
    doesn't support the `github` format, so auto-promoting would turn the
    advertised default (text) report into a hard failure in CI.
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
        elif report_kind is None and os.environ.get("GITHUB_ACTIONS") == "true":
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
    graphs: Mapping[tuple[str | None, str | None], Graph] | None = None,
    cards: list[AgentCard] | None = None,
    agents: list[AgentSummary] | None = None,
) -> None:
    """Dispatch to the chosen renderer and write to stdout.

    `target`/`inventory_tree`/`next_actions` drive the single-target text card and
    `cards` drives the per-agent one; both are ignored by the machine formats
    (github/json), whose stdout stays one payload per scan (ADR-0047). `graphs`
    resolves each finding's lineage against its own agent's graph.
    """
    if output_format == "github":
        rendered = render_github(
            findings,
            posture_findings=posture_findings,
            observations=observations,
            graph=graph,
            graphs=graphs,
        )
    elif output_format == "json":
        rendered = render_json(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_findings,
            observations=observations,
            graph=graph,
            graphs=graphs,
            target=target,
            agents=agents,
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
            graph=graph,
            cards=cards,
        )
    if rendered:
        click.echo(rendered)


def _scan_json_document(
    findings: list[Finding],
    advisory_index: dict[str, dict],
    stats: ScanStats,
    *,
    posture_findings: list[PostureFinding] | None = None,
    observations: list[ObservationFinding] | None = None,
    graph: Graph | None = None,
    graphs: Mapping[tuple[str | None, str | None], Graph] | None = None,
    target: RenderTarget | None = None,
    agents: list[AgentSummary] | None = None,
) -> dict:
    rendered = render_json(
        findings,
        advisory_index,
        stats,
        posture_findings=posture_findings,
        observations=observations,
        graph=graph,
        graphs=graphs,
        target=target,
        agents=agents,
    )
    return json.loads(rendered)


def _emit_triage_report(
    scan_doc: dict,
    *,
    output_format: str,
    output_path: Path | None,
) -> None:
    cards = build_triage_cards(scan_doc)
    if output_format not in {"text", "markdown", "json"}:
        raise click.ClickException(f"--report exposure does not support --format {output_format}")
    rendered = render_triage_report(
        cards, scan_doc, output_format=cast(TriageFormat, output_format)
    )
    if output_path is None:
        click.echo(rendered, nl=not rendered.endswith("\n"))
        return
    try:
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"failed to write report to {output_path}: {exc}") from exc


def _validate_report_options(
    *, report_kind: str | None, output_path: Path | None, output_format: str
) -> None:
    if report_kind is None:
        if output_path is not None:
            raise click.ClickException("--output is only supported with --report exposure")
        if output_format == "markdown":
            raise click.ClickException("--format markdown is only supported with --report exposure")
        return
    if report_kind != "exposure":
        raise click.ClickException(f"unsupported report type: {report_kind}")
    if output_format == "github":
        raise click.ClickException("--report exposure does not support --format github")


def _render_bom_inventory_tree(
    refs: list[ComponentRef],
    findings: list[Finding],
    *,
    declared: bool,
    target: str | None,
    input_path: Path,
    use_color: bool,
    use_unicode: bool,
    graph: Graph | None = None,
    root_label: str | None = None,
) -> str:
    if declared:
        root = Path(target) if target else input_path.parent
        grouped = _group_refs_for_repo_tree(refs)
        return render_repo_inventory_tree(
            root,
            grouped,
            findings,
            use_color=use_color,
            use_unicode=use_unicode,
            graph=graph,
            root_label=root_label,
        )
    return render_inventory_tree(
        refs,
        findings,
        use_color=use_color,
        use_unicode=use_unicode,
        graph=graph,
        root_label=root_label,
    )


@dataclass(frozen=True)
class AgentScanPrep:
    """Everything that differs between an installed and a declared agent.

    Matching, federation, and card assembly are identical for both and stay in
    the shared per-agent loop. `unit_count`/`unit_label` travel with
    `parse_failed` because the repo walk that produced `ScanStats.unit_count`
    for `scan repo` happens in here now.
    """

    manifests: list[tuple[Path, dict]]
    settings_manifests: list[tuple[Path, dict]]
    target_rows: list[tuple[str, str]]
    next_actions: list[str]
    unit_count: int
    unit_label: str
    parse_failed: int


def _no_manifests(*_args: object, **_kwargs: object) -> list[tuple[Path, dict]]:
    """A kind with no filesystem-shaped posture surface yields nothing, rather
    than falling back to another kind's collectors."""
    return []


def _next_actions_for(agent: AgentInstance) -> list[str]:
    actions: list[str] = []
    if agent.project_root is None:
        actions.append("include project-local config: openaca scan endpoint --project .")
    actions.append("emit Agent BOM: openaca bom endpoint --output-dir boms/")
    actions.append("sync to remote: openaca remote sync endpoint")
    return actions


def _agent_scan_prep(
    agent: AgentInstance,
    kind: AgentKind,
    refs: list[ComponentRef],
    *,
    include_gitignored: bool = False,
    repo_parse_cache: dict[tuple[Path, tuple], tuple[int, int]],
) -> AgentScanPrep:
    """`repo_parse_cache` memoizes `parse_repo_grouped`'s `(n_found, n_failed)`
    per `(scan_root, manifest_patterns)`: a many-per-place kind can return
    several declared agents over the *same* root, and walking the repo again for
    each would multiply one malformed manifest into as many failures as there
    are agents. Keying on `manifest_patterns` too means two *different* kinds
    declared over one root each get their own count instead of sharing one."""
    if agent.source == "installed":
        # Read through the *kind's own* installed posture surface, not a
        # Claude-Code-shaped collector called unconditionally for every kind.
        mcp_collector, settings_collector = kind.installed_posture_collectors or (
            _no_manifests,
            _no_manifests,
        )
        return AgentScanPrep(
            manifests=mcp_collector(agent.config_root, agent.project_root, refs),
            settings_manifests=settings_collector(agent.config_root, agent.project_root),
            target_rows=[
                ("config", str(agent.config_root)),
                (
                    "project",
                    str(agent.project_root) if agent.project_root is not None else "not included",
                ),
            ],
            next_actions=_next_actions_for(agent),
            unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
            unit_label="active plugin",
            parse_failed=0,
        )

    # declared: no install state to disambiguate — walk the scan root directly,
    # exactly as `scan repo` does today, so parse failures still count.
    assert agent.scan_root is not None
    mcp_collector, settings_collector = kind.posture_manifest_collectors or (
        _no_manifests,
        _no_manifests,
    )
    cache_key = (agent.scan_root, kind.manifest_patterns)
    if cache_key not in repo_parse_cache:
        # kind.manifest_patterns, not the module-level REGISTRY: a repo declaring
        # two different kinds must not have one kind's manifests count toward the
        # other's evidence gaps.
        parse_groups, n_found = parse_repo_grouped(
            agent.scan_root,
            include_gitignored=include_gitignored,
            registry=kind.manifest_patterns,
        )
        repo_parse_cache[cache_key] = (n_found, n_found - len(parse_groups))
    n_found, n_failed = repo_parse_cache[cache_key]
    return AgentScanPrep(
        manifests=mcp_collector([agent.scan_root], include_gitignored=include_gitignored),
        settings_manifests=settings_collector(
            [agent.scan_root], include_gitignored=include_gitignored
        ),
        target_rows=[("path", str(agent.scan_root))],
        next_actions=[
            f"emit Agent BOM: openaca bom repo --target {agent.scan_root} --output-dir boms/",
        ],
        unit_count=n_found,
        unit_label="manifest",
        parse_failed=n_failed,
    )


def _scan_discovered_agents(
    built: list[tuple[AgentInstance, Graph, list[ComponentRef], list[ComponentRef], list[str]]],
    *,
    include_posture: bool,
    include_gitignored: bool,
    external_scanners: tuple[str, ...],
    output_format: str,
    no_color: bool,
    is_text: bool,
) -> tuple[
    list[Finding],
    list[PostureFinding],
    list[ObservationFinding],
    list[AgentCard],
    list[AgentSummary],
    dict[tuple[str | None, str | None], Graph],
    ScanStats,
    list[dict],
    int,
    dict[str, str],
]:
    """Match, collect, and assemble one card per agent (ADR-0047).

    **One OSV corpus for the whole scan.** Federation is network work, so collect
    the union of every agent's refs, fetch once, then match per agent against the
    shared corpus. Matching is per agent because attribution and lineage come
    from that agent's own graph.
    """
    union_refs = [ref for _a, _g, _ar, refs, _w in built for ref in refs]
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        union_refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)

    findings: list[Finding] = []
    posture_findings: list[PostureFinding] = []
    observations: list[ObservationFinding] = []
    cards: list[AgentCard] = []
    summaries: list[AgentSummary] = []
    graphs: dict[tuple[str | None, str | None], Graph] = {}
    total_parse_failed = 0
    total_unit_count = 0
    unit_label = "manifest"
    repo_parse_cache: dict[tuple[Path, tuple], tuple[int, int]] = {}
    counted_repo_roots: set[tuple[Path, tuple]] = set()

    for agent, graph, agent_all_refs, refs, warnings in built:
        graphs[(agent.kind_id, agent.agent_id)] = graph
        kind = kind_for(agent.kind_id)
        agent_findings = match(
            refs, corpus, graph=graph, agent_kind=agent.kind_id, agent_id=agent.agent_id
        )
        findings.extend(agent_findings)

        agent_observations, scanner_posture = _collect_scanner_findings(
            refs,
            external_scanners=external_scanners,
            skillspector_progress=_skillspector_progress_reporter(output_format),
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
        )
        observations.extend(agent_observations)

        prep = _agent_scan_prep(
            agent,
            kind,
            refs,
            include_gitignored=include_gitignored,
            repo_parse_cache=repo_parse_cache,
        )
        unit_label = prep.unit_label
        # `parse_failed` and `unit_count` are per-(root, kind surface), not
        # per-agent — several same-kind declared agents can share a root, so the
        # scan-wide totals take that pair's counts once while each agent's own
        # coverage below still uses the full (shared) failure count for its root.
        repo_root_key = (
            None if agent.scan_root is None else (agent.scan_root, kind.manifest_patterns)
        )
        if repo_root_key is None or repo_root_key not in counted_repo_roots:
            total_parse_failed += prep.parse_failed
            total_unit_count += prep.unit_count
            if repo_root_key is not None:
                counted_repo_roots.add(repo_root_key)

        agent_posture: list[PostureFinding] | None = None
        if include_posture:
            agent_posture = list(scanner_posture) + run_posture_rules(
                refs,
                prep.manifests,
                prep.settings_manifests,
                allowed_rules=kind.posture_rules,
                agent_kind=agent.kind_id,
                agent_id=agent.agent_id,
            )
            posture_findings.extend(agent_posture)

        coverage = resolve_coverage(
            agent.coverage_baseline, evidence_gaps=len(warnings) + prep.parse_failed
        )
        summaries.append(
            AgentSummary(
                kind=agent.kind_id,
                agent_id=agent.agent_id,
                source=agent.source,
                coverage=coverage,
                host_surface=agent.display_name,
            )
        )
        cards.append(
            AgentCard(
                target=RenderTarget(
                    host_surface=agent.display_name,
                    rows=[*prep.target_rows, ("coverage", coverage)],
                ),
                findings=agent_findings,
                posture_findings=agent_posture,
                observations=agent_observations,
                inventory_tree=_agent_inventory_tree(
                    agent,
                    agent_all_refs,
                    refs,
                    agent_findings,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=graph,
                )
                if is_text
                else None,
                next_actions=prep.next_actions,
                graph=graph,
            )
        )

    stats = ScanStats(
        unit_count=total_unit_count,
        unit_label=unit_label,
        component_count=len(union_refs),
        parse_failed=total_parse_failed,
        sources=_collect_corpus_sources(corpus),
    )
    return (
        findings,
        posture_findings,
        observations,
        cards,
        summaries,
        graphs,
        stats,
        corpus,
        overlay_count,
        overlay_id_map,
    )


def _agent_inventory_tree(
    agent: AgentInstance,
    all_refs: list[ComponentRef],
    refs: list[ComponentRef],
    findings: list[Finding],
    *,
    use_color: bool,
    use_unicode: bool,
    graph: Graph | None,
) -> str | None:
    """Same two renderers, same inputs, as before the agent loop — only the
    selection moved. Declared keeps `scan repo`'s manifest-grouped tree (grouped
    from the unfiltered graph refs, exactly as today); installed keeps the
    endpoint composition tree."""
    if agent.source == "declared":
        assert agent.scan_root is not None
        grouped = _group_refs_for_repo_tree(all_refs)
        if not grouped:
            return None
        return render_repo_inventory_tree(
            agent.scan_root,
            grouped,
            findings,
            use_color=use_color,
            use_unicode=use_unicode,
            graph=graph,
            root_label=agent.display_name,
        )
    return render_inventory_tree(
        refs,
        findings,
        use_color=use_color,
        use_unicode=use_unicode,
        graph=graph,
        root_label=agent.display_name,
    )


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
    *,
    progress: OsvProgressCallback | None = None,
) -> tuple[list[dict], list[str], int, dict[str, str]]:
    """Query OSV for refs and merge OpenACA overlays into returned records."""
    overlays = load_overlays(default_overlays_dir())
    if progress is None:
        corpus, warnings = augment_corpus(refs, [])
    else:
        corpus, warnings = augment_corpus(refs, [], progress=progress)
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


def _write_empty_sarif(sarif: Path) -> None:
    """`--sarif` is a promise to the caller — `action.yml` always passes it and
    publishes the path as an output unconditionally — that a valid SARIF file
    exists once the scan exits 0. Discovery resolving zero agents (an agentless
    repo, no installed runtime) means there is nothing to report, not that the
    promise is void."""
    sarif_doc = to_sarif([], {}, {})
    sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
    click.echo(f"sarif: wrote {sarif}", err=True)


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
@_report_option
@_output_option
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
    report_kind: str | None,
    output_path: Path | None,
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
        ctx,
        sarif,
        fail_on,
        verbose,
        output_format,
        no_color,
        include_posture,
        report_kind=report_kind,
    )
    _validate_report_options(
        report_kind=report_kind, output_path=output_path, output_format=output_format
    )

    is_text = output_format == "text"
    agents = discover_agents(
        DiscoveryContext(source="declared", scan_root=target, include_gitignored=include_gitignored)
    )
    if not agents:
        click.echo(f"{target} declares no agent", err=True)
        if sarif is not None:
            _write_empty_sarif(sarif)
        return

    # The composition graph is the single source of truth (Stage 3): scope and
    # attribution are derived from graph structure, not path heuristics.
    built: list[tuple[AgentInstance, Graph, list[ComponentRef], list[ComponentRef], list[str]]] = []
    for agent in agents:
        warnings: list[str] = []
        graph = build_agent_graph(agent, include_gitignored=include_gitignored, warnings=warnings)
        agent_all_refs = _refs_from_graph(graph)
        # V0: drop software-dependency refs (deps from non-plugin manifests).
        # OpenACA is agent-composition analysis; deps belonging to general
        # software in the repo are out of scope and would mislead users into
        # thinking OpenACA is a general SCA tool. See README for framing.
        refs = build_agent_bom(
            _filter_agent_scope_refs(agent_all_refs),
            target=str(agent.scan_root),
            source_unit_label="manifest",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings)
            ),
        ).component_refs()
        built.append((agent, graph, agent_all_refs, refs, warnings))

    (
        findings,
        posture_findings,
        observations,
        cards,
        summaries,
        graphs,
        stats,
        corpus,
        overlay_count,
        overlay_id_map,
    ) = _scan_discovered_agents(
        built,
        include_posture=include_posture,
        include_gitignored=include_gitignored,
        external_scanners=external_scanners,
        output_format=output_format,
        no_color=no_color,
        is_text=is_text,
    )
    posture_output = posture_findings if include_posture else None
    advisory_index = {a["id"]: a for a in corpus}
    card_target = cards[0].target if cards else None
    refs = [r for _a, _g, _ar, agent_refs, _w in built for r in agent_refs]
    n_found = stats.unit_count
    n_failed = stats.parse_failed
    parse_note = f" ({n_failed} failed to parse)" if n_failed else ""
    grouped = bool(refs)
    # Successfully-parsed manifest count. The pre-agent-loop code held the walk's
    # `parse_groups` list here; only its emptiness is load-bearing, and a
    # per-(root, kind) walk reports the same fact as a count.
    n_parsed = n_found - n_failed

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        if grouped:
            # For text, the tree is in the stdout card; don't duplicate on stderr.
            if not is_text:
                click.echo(
                    f"scanned {n_found} manifest(s), {len(refs)} component(s){parse_note}:",
                    err=True,
                )
                for (agent, graph, agent_all_refs, agent_refs, _w), card in zip(
                    built, cards, strict=True
                ):
                    tree = _agent_inventory_tree(
                        agent,
                        agent_all_refs,
                        agent_refs,
                        card.findings,
                        use_color=_use_color(no_color, output_format),
                        use_unicode=_use_unicode(no_color),
                        graph=graph,
                    )
                    if tree:
                        click.echo(tree, err=True)
        # No graph-projected refs to render: report parse status from the actual
        # filesystem walk (`parse_groups`), not from `grouped`. A manifest that
        # parses cleanly but emits zero refs (empty settings, dep manifest with
        # no deps) contributes no refs yet parsed fine — don't call that a failure.
        elif n_parsed:
            click.echo(
                f"scanned {n_found} manifest(s), 0 component(s){parse_note}",
                err=True,
            )
        elif n_found:
            click.echo(f"found {n_found} manifest file(s) but none parsed successfully", err=True)
        else:
            click.echo(f"no manifests found under {target}", err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f, graph_for(f, None, graphs))}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
            graphs=graphs,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_output,
            observations=observations,
            graphs=graphs,
            target=card_target,
            agents=summaries,
        )
        _emit_triage_report(scan_doc, output_format=output_format, output_path=output_path)
    else:
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
            graphs=graphs,
            cards=cards,
            agents=summaries,
        )

    # For machine formats (github, json), keep the existing one-line stderr
    # summary so consumers parsing only stdout still get totals on stderr.
    # text format's footer already includes them. Parse status comes from the
    # filesystem walk (`parse_groups`), not from the graph-projected `grouped`:
    # a manifest that parses but emits zero refs parsed fine and must not be
    # reported as a parse failure.
    if not n_parsed and output_format != "text":
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
@_report_option
@_output_option
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
    report_kind: str | None,
    output_path: Path | None,
) -> None:
    """Scan the active agent composition installed on this endpoint."""
    sarif, fail_on, verbose, output_format, no_color, include_posture = _apply_group_opts(
        ctx,
        sarif,
        fail_on,
        verbose,
        output_format,
        no_color,
        include_posture,
        report_kind=report_kind,
    )
    _validate_report_options(
        report_kind=report_kind, output_path=output_path, output_format=output_format
    )
    is_text = output_format == "text"
    project_note = str(project) if project is not None else "(none)"

    agents = discover_agents(
        DiscoveryContext(source="installed", config_dir=config_dir, project_root=project)
    )
    if not agents:
        click.echo("no installed agent found", err=True)
        if sarif is not None:
            _write_empty_sarif(sarif)
        return

    built: list[tuple[AgentInstance, Graph, list[ComponentRef], list[ComponentRef], list[str]]] = []
    for agent in agents:
        # Scan-scope transparency. For the default text card the Target block
        # owns this, so the stderr preamble would just precede (and duplicate)
        # the card; emit it only for machine formats or verbose runs.
        if not is_text or verbose:
            click.echo(
                f"detected config_dir={agent.config_root}, project={project_note} (mode=endpoint)",
                err=True,
            )
        warnings: list[str] = []
        graph = build_agent_graph(agent, warnings=warnings)
        agent_all_refs = _refs_from_graph(graph)
        refs = build_agent_bom(
            _filter_agent_scope_refs(agent_all_refs),
            target=str(agent.config_root),
            source_unit_count=sum(1 for r in agent_all_refs if _is_plugin_ref(r)),
            source_unit_label="active plugin",
            graph=graph,
            agent_kind=agent.kind_id,
            agent_id=agent.agent_id,
            agent_name=agent.display_name,
            composition_source=agent.source,
            composition_coverage=resolve_coverage(
                agent.coverage_baseline, evidence_gaps=len(warnings)
            ),
        ).component_refs()
        built.append((agent, graph, agent_all_refs, refs, warnings))

    (
        findings,
        posture_findings,
        observations,
        cards,
        summaries,
        graphs,
        stats,
        corpus,
        overlay_count,
        overlay_id_map,
    ) = _scan_discovered_agents(
        built,
        include_posture=include_posture,
        include_gitignored=False,
        external_scanners=external_scanners,
        output_format=output_format,
        no_color=no_color,
        is_text=is_text,
    )
    posture_output = posture_findings if include_posture else None
    advisory_index = {a["id"]: a for a in corpus}
    card_target = cards[0].target if cards else None

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        for _agent, _graph, _all_refs, _refs, agent_warnings in built:
            for w in agent_warnings:
                click.echo(f"  warning: {w}", err=True)
        # For text, the tree is in the stdout card; don't duplicate on stderr.
        if not is_text:
            for (agent, graph, agent_all_refs, refs, _w), card in zip(built, cards, strict=True):
                tree = _agent_inventory_tree(
                    agent,
                    agent_all_refs,
                    refs,
                    card.findings,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=graph,
                )
                if tree:
                    click.echo(tree, err=True)
        for line in _federation_targets_lines(
            [r for _a, _g, _ar, refs, _w in built for r in refs], len(corpus)
        ):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f, graph_for(f, None, graphs))}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
            graphs=graphs,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)
    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_output,
            observations=observations,
            graphs=graphs,
            target=card_target,
            agents=summaries,
        )
        _emit_triage_report(scan_doc, output_format=output_format, output_path=output_path)
    else:
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
            graphs=graphs,
            cards=cards,
            agents=summaries,
        )
    _stderr_summary(findings, f"resolved {stats.unit_count} active plugin(s)", output_format)

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


def _load_bom_documents(path: Path, raw: str) -> list[dict]:
    """One JSON object, or NDJSON with one document per line.

    `openaca bom endpoint > bom.json` emits NDJSON now (one document per agent),
    so the consumer of that file has to read it.
    """
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        documents: list[dict] = []
        for number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as exc:
                raise click.ClickException(f"{path}:{number}: invalid JSON — {exc}") from exc
            if not isinstance(parsed, dict):
                raise click.ClickException(
                    f"{path}:{number}: BOM must be a JSON object, got {type(parsed).__name__}"
                )
            documents.append(parsed)
        if not documents:
            raise click.ClickException(f"{path}: no BOM documents found")
        return documents
    if not isinstance(doc, dict):
        raise click.ClickException(f"{path}: BOM must be a JSON object, got {type(doc).__name__}")
    return [doc]


def _is_graph_backed_bom(doc: dict[str, object]) -> bool:
    """Does this BOM encode the OpenACA composition graph (vs. a flat BOM)?

    True for the legacy logical target key (_TARGET_KEY) and for any
    agent-rooted document. A plain CycloneDX metadata.component with any other
    bom-ref is NOT graph-backed: rebuilding its graph would re-derive top-level
    packages as software-dependency and drop them. Such BOMs take the flat path
    (stored openaca:scope). ADR-0045 makes this prefix load-bearing: misreading
    it silently drops agent-dependency findings.
    """
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return False
    component = metadata.get("component")
    if not isinstance(component, dict):
        return False
    ref = component.get("bom-ref")
    if not isinstance(ref, str):
        return False
    return ref == _TARGET_KEY or ref.startswith(AGENT_ROOT_PREFIX)


@main.command(name="bom")
@click.pass_context
@_bom_input_option
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_report_option
@_output_option
def scan_bom(
    ctx: click.Context,
    input_path: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    report_kind: str | None,
    output_path: Path | None,
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
        report_kind=report_kind,
    )
    group_opts = (ctx.parent.obj if ctx.parent else None) or {}
    if include_posture or group_opts.get("include_posture"):
        raise click.ClickException(
            "--include-posture is not supported for scan bom; posture checks "
            "require the original repo or endpoint configuration."
        )
    _validate_report_options(
        report_kind=report_kind, output_path=output_path, output_format=output_format
    )
    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise click.ClickException(f"{input_path}: not valid UTF-8 — {exc}") from exc
    documents = _load_bom_documents(input_path, raw)

    # A graph-backed BOM (this branch's emitter) carries metadata.component plus
    # real dependencies[] edges, so the reconstructed graph round-trips scope +
    # attribution from structure (the openaca:attributed_to property is gone).
    # The robust signal is the metadata.component bom-ref: a graph-backed BOM
    # sets it to the legacy logical target key or the agent-root prefix.
    # A flat/external or pre-Stage-4 BOM has no metadata.component OR has one
    # whose bom-ref is some other component (a plain CycloneDX metadata.component
    # is a standard field unrelated to OpenACA's graph encoding). For those,
    # graph_from_cyclonedx would synthesize a target and attach every component
    # directly under it, re-deriving package scope as software-dependency and
    # silently dropping agent-dependency findings. So read the stored
    # openaca:scope off each component instead and thread graph=None.
    #
    # `docs_built` carries the source `doc` alongside each entry: the second loop
    # reads legacy target metadata per document, and a bare loop variable would
    # not survive past the first loop.
    docs_built: list[tuple[dict, AgentInfo | None, Graph | None, list[ComponentRef]]] = []
    for doc in documents:
        agent_info = agent_info_from_cyclonedx(doc)
        doc_graph: Graph | None
        if _is_graph_backed_bom(doc):
            doc_graph = graph_from_cyclonedx(doc)
            doc_refs = build_agent_bom(
                _filter_agent_scope_refs(_refs_from_graph(doc_graph)),
                target_type="bom",
                target=str(input_path),
                graph=doc_graph,
            ).component_refs()
        else:
            doc_graph = None
            doc_refs = build_agent_bom(
                _filter_agent_scope_refs(component_refs_from_cyclonedx(doc)),
                target_type="bom",
                target=str(input_path),
            ).component_refs()
        docs_built.append((doc, agent_info, doc_graph, doc_refs))

    # Each NDJSON document carries its own openaca:source_unit_count/label (one
    # per agent); sum the counts so the reported total covers every document's
    # components, not just the first agent's. A document missing a count (a
    # stored pre-agent-metadata BOM) contributes 1, mirroring the single-BOM
    # fallback used everywhere else this value is consumed. A shared label is
    # kept as-is; a mix of labels (a repo declaring more than one agent kind)
    # falls back to "unit" rather than misreporting one agent's units as
    # another's.
    unit_pairs = [source_unit_from_cyclonedx(doc) for doc in documents]
    source_unit_count = sum(count if count is not None else 1 for count, _label in unit_pairs)
    unit_labels = {label for _count, label in unit_pairs if label}
    source_unit_label = (
        next(iter(unit_labels)) if len(unit_labels) == 1 else ("unit" if unit_labels else None)
    )
    refs = [ref for _d, _i, _g, doc_refs in docs_built for ref in doc_refs]
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)

    is_text = output_format == "text"
    findings: list[Finding] = []
    observations: list[ObservationFinding] = []
    cards: list[AgentCard] = []
    summaries: list[AgentSummary] = []
    graphs: dict[tuple[str | None, str | None], Graph] = {}
    for doc, agent_info, doc_graph, doc_refs in docs_built:
        doc_findings = match(
            doc_refs,
            corpus,
            graph=doc_graph,
            agent_kind=agent_info.kind if agent_info else None,
            agent_id=agent_info.agent_id if agent_info else None,
        )
        findings.extend(doc_findings)
        bom_rows: list[tuple[str, str]] = [("file", str(input_path))]
        if agent_info is not None:
            if doc_graph is not None:
                graphs[(agent_info.kind, agent_info.agent_id)] = doc_graph
            bom_rows.append(("agent", f"{agent_info.kind} ({agent_info.source or 'unknown'})"))
            bom_rows.append(("coverage", agent_info.coverage or "unknown"))
            summaries.append(
                AgentSummary(
                    kind=agent_info.kind,
                    agent_id=agent_info.agent_id,
                    source=agent_info.source or "bom",
                    coverage=agent_info.coverage or "unknown",
                    host_surface=agent_info.name or agent_info.kind,
                )
            )
        else:
            # A stored `0.4` document (or any pre-agent-metadata flat BOM) has no
            # agent metadata to read back. Carry that through rather than
            # inventing metadata the document never had, or rejecting a document
            # the compatibility contract requires us to read.
            doc_target_type, doc_target = target_info_from_cyclonedx(doc)
            orig = (
                f"{doc_target_type} {doc_target}".strip()
                if doc_target_type and doc_target
                else "unknown"
            )
            bom_rows.append(("original target", orig))
            summaries.append(
                AgentSummary(
                    kind=None,
                    agent_id=None,
                    source="bom",
                    coverage="unknown",
                    host_surface="stored BOM",
                )
            )
        # The repo-grouped inventory tree keys on "was this composition
        # declared", which is what `target_type: repo` was standing in for.
        doc_declared = (
            agent_info.source == "declared"
            if agent_info is not None
            else target_info_from_cyclonedx(doc)[0] == "repo"
        )
        cards.append(
            AgentCard(
                target=RenderTarget(host_surface=summaries[-1].host_surface, rows=bom_rows),
                findings=doc_findings,
                graph=doc_graph,
                inventory_tree=_render_bom_inventory_tree(
                    doc_refs,
                    doc_findings,
                    declared=doc_declared,
                    target=target_info_from_cyclonedx(doc)[1],
                    input_path=input_path,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=doc_graph,
                    root_label=agent_info.name or agent_info.kind if agent_info else None,
                )
                if is_text
                else None,
            )
        )
    advisory_index = {a["id"]: a for a in corpus}
    card_target = cards[0].target if cards else None

    if verbose:
        click.echo(f"loaded {overlay_count} OpenACA overlay(s)", err=True)
        unit_count = source_unit_count if source_unit_count is not None else 1
        unit_label = source_unit_label or "agent BOM"
        # For text, the tree is in the stdout card; don't duplicate on stderr.
        if not is_text:
            click.echo(f"scanned {unit_count} {unit_label}(s), {len(refs)} component(s):", err=True)
            for doc, agent_info, doc_graph, doc_refs in docs_built:
                doc_declared = (
                    agent_info.source == "declared"
                    if agent_info is not None
                    else target_info_from_cyclonedx(doc)[0] == "repo"
                )
                tree = _render_bom_inventory_tree(
                    doc_refs,
                    findings,
                    declared=doc_declared,
                    target=target_info_from_cyclonedx(doc)[1],
                    input_path=input_path,
                    use_color=_use_color(no_color, output_format),
                    use_unicode=_use_unicode(no_color),
                    graph=doc_graph,
                    root_label=agent_info.name or agent_info.kind if agent_info else None,
                )
                if tree:
                    click.echo(tree, err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f, graph_for(f, None, graphs))}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings, advisory_index, overlay_id_map, observations=None, graphs=graphs
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=source_unit_count if source_unit_count is not None else 1,
        unit_label=source_unit_label or "agent BOM",
        component_count=len(refs),
        sources=_collect_corpus_sources(corpus),
    )
    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            observations=observations,
            graphs=graphs,
            target=card_target,
            agents=summaries,
        )
        _emit_triage_report(scan_doc, output_format=output_format, output_path=output_path)
    else:
        _emit(
            findings,
            advisory_index,
            stats,
            output_format=output_format,
            use_color=_use_color(no_color, output_format),
            verbose=verbose,
            observations=observations,
            target=card_target,
            graphs=graphs,
            cards=cards,
            agents=summaries,
        )
    unit_count = source_unit_count if source_unit_count is not None else 1
    unit_label = source_unit_label or "agent BOM"
    _stderr_summary(
        findings,
        f"scanned {unit_count} {unit_label}(s), {len(refs)} component(s)",
        output_format,
    )
    _exit_for_findings(fail_on, findings)


if __name__ == "__main__":
    main()
