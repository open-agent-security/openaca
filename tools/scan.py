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
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import click
from click.core import ParameterSource

from tools.bom import (
    build_agent_bom,
    component_refs_from_cyclonedx,
    graph_from_cyclonedx,
    scanned_hosts_from_cyclonedx,
    source_unit_from_cyclonedx,
    target_info_from_cyclonedx,
)
from tools.component_ref import ComponentRef
from tools.endpoint_request import resolve_endpoint_request
from tools.graph import Graph
from tools.graph_build import _TARGET_KEY, build_graph
from tools.host_paths import resolved_owner
from tools.hosts import DEFAULT_HOST_ID, HOSTS, all_host_ids, plugin_unit_label
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
from tools.posture import (
    PostureFinding,
    collect_endpoint_posture_inputs,
    collect_mcp_manifests,
    collect_settings_manifests,
    run_posture_rules,
)
from tools.posture.rules.api_endpoint_override import RULE_ID as _API_ENDPOINT_OVERRIDE_RULE_ID
from tools.render import (
    RenderTarget,
    ScanStats,
    compute_components_by_host,
    hosts_from_refs,
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


def _repo_manifest_hosts(
    manifests: list[tuple[Path, dict]], refs: list[ComponentRef]
) -> dict[Path, str]:
    """Map each `collect_mcp_manifests`-returned path to the single host the
    graph parsed its MCP server children as (`ref.extra["runtime_hosts"]`),
    for `resolved_owner` to prefer over `owning_host`'s directory-shape guess.

    Repo mode has no per-host collector call to record provenance during
    collection (unlike endpoint mode's `collect_endpoint_posture_inputs`), so
    this reconstructs it after the fact from the graph's own `mcp_server`
    refs, matched by resolved path.
    """
    by_resolved: dict[Path, str] = {}
    for ref in refs:
        extra = ref.extra or {}
        if extra.get("component_type") != "mcp_server" or not ref.source_manifest:
            continue
        runtime_hosts = extra.get("runtime_hosts")
        if not isinstance(runtime_hosts, list) or len(runtime_hosts) != 1:
            continue
        try:
            resolved = Path(ref.source_manifest).resolve()
        except (OSError, RuntimeError):
            continue
        by_resolved[resolved] = runtime_hosts[0]
    out: dict[Path, str] = {}
    for path, _ in manifests:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue
        host_id = by_resolved.get(resolved)
        if host_id is not None:
            out[path] = host_id
    return out


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def _is_under_any(path: Path, roots: list[Path]) -> bool:
    resolved = _safe_resolve(path)
    if resolved is None:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def _is_losing_plugin_manifest(path: Path, realized_manifest_by_root: dict[Path, Path]) -> bool:
    """True for a native `plugin.json` that lost the realization race to a
    sibling manifest in the same bundle root (see `realized_plugin_manifests`
    on `build_graph`). Non-plugin manifests (`mcp.json`, etc.) never collide
    this way, so they always return False here."""
    if path.name != "plugin.json" or path.parent.name not in (
        ".claude-plugin",
        ".cursor-plugin",
    ):
        return False
    resolved = _safe_resolve(path)
    if resolved is None:
        return False
    root_resolved = _safe_resolve(path.parent.parent)
    if root_resolved is None:
        return False
    winning = realized_manifest_by_root.get(root_resolved)
    return winning is not None and winning != resolved


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
) -> tuple[list[ObservationFinding], list[PostureFinding]]:
    observations = collect_skill_observations(refs)
    posture_findings: list[PostureFinding] = []
    if "nvidia-skillspector" in external_scanners:
        try:
            skillspector_findings = collect_skillspector_findings(
                refs, progress=skillspector_progress
            )
        except SkillSpectorCommandNotFound as exc:
            raise click.ClickException(str(exc)) from exc
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
_host_option = click.option(
    "--host",
    "host_values",
    multiple=True,
    default=(),
    help=(
        "Host(s) to scan for (repeatable or comma-separated). Known hosts: "
        f"{', '.join(all_host_ids())}. Omitted: every known host."
    ),
)
_endpoint_host_option = click.option(
    "--host",
    "host_values",
    multiple=True,
    default=(),
    help=(
        "Host(s) to scan on this endpoint (repeatable or comma-separated). Known hosts: "
        f"{', '.join(all_host_ids())}. Omitted: every host detected on this machine. "
        "A named host that is not detected is an error unless --config-dir supplies "
        "its config root."
    ),
)


def _resolve_hosts(host_values: tuple[str, ...]) -> list[str]:
    """Flatten repeatable/comma-separated --host values; omitted = every known host."""
    if not host_values:
        return all_host_ids()
    known = set(all_host_ids())
    resolved: list[str] = []
    for raw in host_values:
        for piece in raw.split(","):
            host_id = piece.strip()
            if not host_id:
                continue
            if host_id not in known:
                raise click.BadParameter(
                    f"unknown host {host_id!r}; known hosts: {', '.join(sorted(known))}"
                )
            if host_id not in resolved:
                resolved.append(host_id)
    if not resolved:
        # --host was given but every piece was empty after stripping
        # (e.g. "--host ','" or "--host ''") — an explicit, unusable
        # value is an error, not indistinguishable from "scanned zero
        # hosts on purpose."
        raise click.BadParameter(
            "--host given but contains no usable host name "
            f"(known hosts: {', '.join(sorted(known))})"
        )
    return resolved


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
) -> None:
    """Dispatch to the chosen renderer and write to stdout.

    `target`/`inventory_tree`/`next_actions` drive the text card and are ignored
    by the machine formats (github/json), whose stdout shape is unchanged.
    """
    if output_format == "github":
        rendered = render_github(
            findings, posture_findings=posture_findings, observations=observations, graph=graph
        )
    elif output_format == "json":
        rendered = render_json(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_findings,
            observations=observations,
            graph=graph,
            target=target,
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
    target: RenderTarget | None = None,
) -> dict:
    rendered = render_json(
        findings,
        advisory_index,
        stats,
        posture_findings=posture_findings,
        observations=observations,
        graph=graph,
        target=target,
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
    target_type: str | None,
    target: str | None,
    input_path: Path,
    use_color: bool,
    use_unicode: bool,
    graph: Graph | None = None,
    hosts: list[str] | None = None,
) -> str:
    if target_type == "repo":
        root = Path(target) if target else input_path.parent
        grouped = _group_refs_for_repo_tree(refs)
        return render_repo_inventory_tree(
            root,
            grouped,
            findings,
            use_color=use_color,
            use_unicode=use_unicode,
            graph=graph,
            hosts=hosts,
        )
    return render_inventory_tree(
        refs, findings, use_color=use_color, use_unicode=use_unicode, graph=graph, hosts=hosts
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
@_host_option
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
    host_values: tuple[str, ...],
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

    hosts = _resolve_hosts(host_values)

    # The composition graph is the single source of truth (Stage 3): scope and
    # attribution are derived from graph structure, not path heuristics.
    # `excluded_plugin_roots` is populated with every native plugin bundle root
    # the graph discovered but declined to realize because its owning host
    # isn't selected — posture manifest collection below needs the same
    # boundary (see `manifest_hosts` comment). `realized_plugin_manifests` is
    # populated with the manifest path that actually won realization for
    # every root that DID realize, for the losing-sibling filter below.
    excluded_plugin_roots: list[Path] = []
    realized_plugin_manifests: dict[Path, Path] = {}
    graph = build_graph(
        target,
        mode="repo",
        include_gitignored=include_gitignored,
        hosts=hosts,
        excluded_plugin_roots=excluded_plugin_roots,
        realized_plugin_manifests=realized_plugin_manifests,
    )
    all_refs = _refs_from_graph(graph)
    # Reconstruct the per-manifest `grouped` list the repo renderer expects by
    # grouping the projected refs by their source_manifest Path; the renderer is
    # unchanged and reads graph-derived scope/attribution off each ref.
    grouped = _group_refs_for_repo_tree(all_refs)
    # Manifest-visited count and parse-failure reporting are properties of the
    # filesystem walk, not the graph; source them from the walk so the scanned/
    # failed-to-parse summary is unchanged. (No scope/attribution comes from
    # here — that is graph-derived.)
    parse_groups, n_found = parse_repo_grouped(
        target, include_gitignored=include_gitignored, hosts=hosts
    )
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
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus, graph=graph)
    observations, scanner_posture_findings = _collect_scanner_findings(
        refs,
        external_scanners=external_scanners,
        skillspector_progress=_skillspector_progress_reporter(output_format),
    )

    posture_findings: list[PostureFinding] = []
    if include_posture:
        posture_findings.extend(scanner_posture_findings)
        manifests = collect_mcp_manifests([target], include_gitignored=include_gitignored)
        # `owning_host`'s directory-shape heuristic only recognizes the literal
        # `.cursor/mcp.json` convention, so a Cursor plugin's bundled
        # `<plugin-root>/mcp.json` (e.g. `plugins/local/<name>/mcp.json`) would
        # otherwise misattribute to Claude Code. `manifest_hosts` overrides it
        # with the graph's own parse provenance (`runtime_hosts` on each
        # bundled MCP server ref), same as `resolved_owner` already does for
        # endpoint mode's collection provenance.
        manifest_hosts = _repo_manifest_hosts(manifests, all_refs)
        # A bundle whose owning host isn't selected has no `manifest_hosts`
        # entry (the graph never realized it, so it produced no `mcp_server`
        # ref to reconstruct provenance from) and `owning_host`'s path-shape
        # fallback doesn't recognize its bundled manifest either, so it would
        # otherwise fall back to "claude-code" and leak through the `hosts`
        # filter below even though the user excluded that host. Drop anything
        # under `excluded_plugin_roots` outright rather than let it reach the
        # fallback.
        excluded_resolved = [_safe_resolve(p) for p in excluded_plugin_roots]
        excluded_resolved = [p for p in excluded_resolved if p is not None]
        # When a bundle root carries both native manifest formats and both
        # are valid, graph realization picks exactly one (Claude-format
        # wins — see `_find_plugin_roots`). The losing sibling produced no
        # `mcp_server` refs, so it has no `manifest_hosts` entry either, and
        # `owning_host`'s path-shape fallback doesn't recognize a bundled
        # `.cursor-plugin/plugin.json` — it would misattribute the loser's
        # own inline `mcpServers` to whichever host `owning_host` defaults
        # to. Drop any `plugin.json` manifest that isn't the one that
        # actually realized its bundle root.
        realized_manifest_by_root = {
            resolved_root: resolved_manifest
            for root, manifest in realized_plugin_manifests.items()
            if (resolved_root := _safe_resolve(root)) is not None
            and (resolved_manifest := _safe_resolve(manifest)) is not None
        }
        manifests = [
            (p, d)
            for p, d in manifests
            if resolved_owner(p, manifest_hosts) in hosts
            and not _is_under_any(p, excluded_resolved)
            and not _is_losing_plugin_manifest(p, realized_manifest_by_root)
        ]
        active_rule_ids = frozenset().union(*(HOSTS[h].posture_rule_ids for h in hosts))
        settings_manifests = (
            collect_settings_manifests([target], include_gitignored=include_gitignored)
            if _API_ENDPOINT_OVERRIDE_RULE_ID in active_rule_ids
            else []
        )
        # The settings walk is an independent filesystem pass of the same
        # tree — it must honor the unselected-host bundle boundary the MCP
        # manifest list above already does, or a `.claude/settings.json`
        # inside an excluded bundle still produces posture findings.
        settings_manifests = [
            (p, d) for p, d in settings_manifests if not _is_under_any(p, excluded_resolved)
        ]
        posture_findings.extend(
            run_posture_rules(refs, manifests, settings_manifests, manifest_hosts)
        )

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
            hosts=hosts,
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
                    hosts=hosts,
                )
                if tree:
                    click.echo(tree, err=True)
        # No graph-projected refs to render: report parse status from the actual
        # filesystem walk (`parse_groups`), not from `grouped`. A manifest that
        # parses cleanly but emits zero refs (empty settings, dep manifest with
        # no deps) contributes no refs yet parsed fine — don't call that a failure.
        elif parse_groups:
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
                click.echo(f"  {_finding_line(f, graph)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
            graph=graph,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=n_found,
        unit_label="manifest",
        component_count=len(refs),
        components_by_host=compute_components_by_host(refs, graph, hosts=hosts),
        parse_failed=n_failed,
        sources=_collect_corpus_sources(corpus),
    )
    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_output,
            observations=observations,
            graph=graph,
            target=card_target,
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
            inventory_tree=card_tree,
            next_actions=card_next,
            graph=graph,
        )

    # For machine formats (github, json), keep the existing one-line stderr
    # summary so consumers parsing only stdout still get totals on stderr.
    # text format's footer already includes them. Parse status comes from the
    # filesystem walk (`parse_groups`), not from the graph-projected `grouped`:
    # a manifest that parses but emits zero refs parsed fine and must not be
    # reported as a parse failure.
    if not parse_groups and output_format != "text":
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
@_endpoint_host_option
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
    host_values: tuple[str, ...],
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
    selected_hosts, host_roots = resolve_endpoint_request(host_values, config_dir)
    # The primary host's root stays the scan anchor: the BOM `target` string,
    # the posture-manifest scope, and `build_graph`'s API-compatibility target.
    config_dir = host_roots[selected_hosts[0]]

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

    warnings: list[str] = []
    graph = build_graph(
        config_dir,
        mode="endpoint",
        project_root=project,
        warnings=warnings,
        host_config_roots=host_roots,
    )
    refs = _refs_from_graph(graph)
    # ADR-0045 Decision #7: Cursor plugins are presence-only, so a selection that includes
    # it must not claim "active plugin" — same rule bom_cli.py's `bom
    # endpoint` applies to openaca:source_unit_label, shared via tools.hosts
    # so the wording can't drift between the two surfaces.
    plugin_unit = plugin_unit_label(selected_hosts)
    refs = build_agent_bom(
        _filter_agent_scope_refs(refs),
        target_type="endpoint",
        target=str(config_dir),
        source_unit_count=sum(1 for r in refs if _is_plugin_ref(r)),
        source_unit_label=plugin_unit,
        graph=graph,
    ).component_refs()
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus, graph=graph)
    observations, scanner_posture_findings = _collect_scanner_findings(
        refs,
        external_scanners=external_scanners,
        skillspector_progress=_skillspector_progress_reporter(output_format),
    )

    posture_findings: list[PostureFinding] = []
    if include_posture:
        posture_findings.extend(scanner_posture_findings)
        manifests, manifest_hosts, settings_manifests = collect_endpoint_posture_inputs(
            host_roots, project, refs
        )
        posture_findings.extend(
            run_posture_rules(refs, manifests, settings_manifests, manifest_hosts=manifest_hosts)
        )

    # None means posture was not requested (rendered as "skipped"); [] means it ran and
    # found nothing. Don't collapse the empty-but-ran case to None.
    posture_output = posture_findings if include_posture else None

    advisory_index = {a["id"]: a for a in corpus}
    plugin_count = sum(1 for r in refs if _is_plugin_ref(r))

    # Inventory tree for the text card (default stdout). Machine formats keep the
    # tree as a verbose-stderr diagnostic only (below).
    config_rows = (
        [("config", str(config_dir))]
        if len(selected_hosts) == 1
        else [(f"config ({h})", str(host_roots[h])) for h in selected_hosts]
    )
    card_target = RenderTarget(
        host_surface=_host_surface_label(selected_hosts),
        rows=[
            *config_rows,
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
            hosts=selected_hosts,
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
                hosts=selected_hosts,
            )
            if tree:
                click.echo(tree, err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f, graph)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings,
            advisory_index,
            overlay_id_map,
            posture_findings=posture_output,
            observations=observations or None,
            graph=graph,
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=plugin_count,
        unit_label=plugin_unit,
        component_count=len(refs),
        components_by_host=compute_components_by_host(refs, graph, hosts=selected_hosts),
        sources=_collect_corpus_sources(corpus),
    )
    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            posture_findings=posture_output,
            observations=observations,
            graph=graph,
            target=card_target,
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
            inventory_tree=card_tree,
            next_actions=card_next,
            graph=graph,
        )
    _stderr_summary(findings, f"resolved {plugin_count} {plugin_unit}(s)", output_format)

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


def _is_graph_backed_bom(doc: dict[str, object]) -> bool:
    """Does this BOM encode the OpenACA composition graph (vs. a flat BOM)?

    True iff metadata.component's bom-ref is the stable logical target key
    (_TARGET_KEY) that build_agent_bom(graph=...) emits. A plain CycloneDX
    metadata.component with any other bom-ref is NOT graph-backed: rebuilding
    its graph would re-derive top-level packages as software-dependency and
    drop them. Such BOMs take the flat path (stored openaca:scope).
    """
    metadata = doc.get("metadata")
    if not isinstance(metadata, dict):
        return False
    component = metadata.get("component")
    if not isinstance(component, dict):
        return False
    return component.get("bom-ref") == _TARGET_KEY


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
    # A graph-backed BOM (this branch's emitter) carries metadata.component plus
    # real dependencies[] edges, so the reconstructed graph round-trips scope +
    # attribution from structure (the openaca:attributed_to property is gone).
    # The robust signal is the metadata.component bom-ref: a graph-backed BOM
    # sets it to the stable logical target key (_TARGET_KEY == "openaca:target",
    # emitted as target_bom_ref=graph.root.key by _build_agent_bom_from_graph).
    # A flat/external or pre-Stage-4 BOM has no metadata.component OR has one
    # whose bom-ref is some other component (a plain CycloneDX metadata.component
    # is a standard field unrelated to OpenACA's graph encoding). For those,
    # graph_from_cyclonedx would synthesize a target and attach every component
    # directly under it, re-deriving package scope as software-dependency and
    # silently dropping agent-dependency findings. So read the stored
    # openaca:scope off each component instead and thread graph=None.
    graph: Graph | None
    if _is_graph_backed_bom(doc):
        graph = graph_from_cyclonedx(doc)
        refs = build_agent_bom(
            _filter_agent_scope_refs(_refs_from_graph(graph)),
            target_type="bom",
            target=str(input_path),
            graph=graph,
        ).component_refs()
    else:
        graph = None
        refs = build_agent_bom(
            _filter_agent_scope_refs(component_refs_from_cyclonedx(doc)),
            target_type="bom",
            target=str(input_path),
        ).component_refs()
    corpus, fed_warnings, overlay_count, overlay_id_map = _load_osv_with_overlays(
        refs, progress=_osv_progress_reporter(output_format)
    )
    _stamp_source(corpus, "osv.dev")
    for fw in fed_warnings:
        click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus, graph=graph)
    observations = []
    advisory_index = {a["id"]: a for a in corpus}

    # The scanned host list, for per-host tags/breakdown. Primary source is
    # the `openaca:scanned_hosts` metadata property, which every BOM this
    # version writes carries; the fallback is for BOMs written before it was
    # unconditional and derives the distinct hosts `_ref_hosts` attributes
    # across `refs` (ancestry via `graph` when graph-backed, else each ref's
    # own `runtime_hosts`/default). The fallback cannot distinguish "scanned
    # Cursor, found nothing" from "scanned Claude Code, found nothing" — an
    # empty BOM has no components to infer from — which is exactly why the
    # property is no longer gated on multi-host at the write side.
    hosts = scanned_hosts_from_cyclonedx(doc)
    if hosts is None:
        hosts = hosts_from_refs(refs, graph)

    # Inventory tree for the text card; machine formats keep it verbose-only.
    is_text = output_format == "text"
    bom_rows: list[tuple[str, str]] = [("file", str(input_path))]
    if target_type:
        orig = f"{target_type} {target}".strip() if target else target_type
        bom_rows.append(("original target", orig))
    # A BOM's Target block says "Agent BOM" where a live endpoint scan says
    # "host surface: Cursor", so nothing else in the text card names the
    # host(s) the BOM captured: the inventory tree's `[<host>]` tags are
    # 2+-host-gated (with one host they'd be noise on every line), and a BOM
    # with zero components has no tree entries to tag at all. State it here
    # instead, on the same "explicitly not the legacy default" condition the
    # repo tree's tags use, so the one case that reads as Claude Code by
    # default is the only one left unlabeled.
    if hosts != [DEFAULT_HOST_ID]:
        bom_rows.append(("hosts", ", ".join(hosts)))
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
            graph=graph,
            hosts=hosts,
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
                graph=graph,
                hosts=hosts,
            )
            if tree:
                click.echo(tree, err=True)
        for line in _federation_targets_lines(refs, len(corpus)):
            click.echo(line, err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f, graph)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(
            findings, advisory_index, overlay_id_map, observations=None, graph=graph
        )
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    stats = ScanStats(
        unit_count=source_unit_count if source_unit_count is not None else 1,
        unit_label=source_unit_label or "agent BOM",
        component_count=len(refs),
        components_by_host=compute_components_by_host(refs, graph, hosts=hosts),
        sources=_collect_corpus_sources(corpus),
    )
    if report_kind == "exposure":
        scan_doc = _scan_json_document(
            findings,
            advisory_index,
            stats,
            observations=observations,
            graph=graph,
            target=card_target,
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
            inventory_tree=card_tree,
            graph=graph,
        )
    unit_count = source_unit_count if source_unit_count is not None else 1
    unit_label = source_unit_label or "agent BOM"
    _stderr_summary(
        findings,
        f"scanned {unit_count} {unit_label}(s), {len(refs)} component(s)",
        output_format,
    )
    _exit_for_findings(fail_on, findings)


_HOST_SURFACE_NAMES = {"claude-code": "Claude Code", "cursor": "Cursor"}


def _host_surface_label(host_ids: list[str]) -> str:
    return ", ".join(_HOST_SURFACE_NAMES.get(h, h) for h in host_ids)


if __name__ == "__main__":
    main()
