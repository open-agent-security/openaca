"""End-to-end ASVE scan: parse → match → report (SARIF + annotations).

Two modes via subcommands (per ADR-0006); a subcommand is required:

    asve-scan repo --target <repo> --advisories <dir> [...]
        Walks supported agent-stack manifests committed in the target
        repository. Covers (a) project-host config under `.claude/*`
        (which describes what Claude Code loads when run in this repo,
        i.e. developer-agent posture committed to source), and
        (b) manifest-backed SDK config like a root `.mcp.json` an app
        loads via `query({ options: { mcpConfig: "..." } })`. Does NOT
        cover SDK-inline definitions, code-registered tools, or anything
        requiring source-code extraction — those are V1. Treat repo
        findings as *declared* composition, not deployed-app
        composition.

    asve-scan endpoint [--config-dir <claude-config-dir>] [--project <repo>]
        --advisories <dir> [...]
        Install-state-aware endpoint scan: reads settings.json +
        installed_plugins.json to enumerate the active agent stack. Defaults
        to $CLAUDE_CONFIG_DIR, else ~/.claude. --project layers project/local
        settings when scanning a repo's endpoint context.

Common options (--sarif, --fail-on, -v) can be placed before or after the
subcommand name; the group forwards them either way:

    asve-scan -v repo --target X --advisories Y
    asve-scan repo --target X --advisories Y -v   # equivalent

Findings carry an optional `attributed_to` field (e.g.,
"claude-plugin/<name>@<version>") set by parsers when a component was
discovered via an active plugin. Output prefixes the finding with `via <X>`
when present; SARIF surfaces it in `properties.attributed_to`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import yaml
from click.core import ParameterSource

from tools.component_ref import ComponentRef
from tools.matcher import Finding, match
from tools.osv_federation import augment_corpus, collect_target_purls, is_queryable
from tools.parsers import flatten_grouped, parse_repo_grouped
from tools.parsers.claude_install import parse_install
from tools.render import (
    ScanStats,
    render_github,
    render_inventory_tree,
    render_json,
    render_text,
)
from tools.sarif import to_sarif

_FORMAT_CHOICES = ("text", "github", "json")

# `--db` choices. V0 ships two combinations; the comma form is a literal
# string the user types — Click `Choice` does exact matching. Adding a
# third backend later (e.g., GHSA direct) means appending to this tuple
# and teaching `_parse_db_selection` the new token.
_DB_CHOICES = ("asve", "asve,osv")


def _parse_db_selection(value: str) -> set[str]:
    """Tokenize a `--db` value into a set of backend names."""
    return {tok.strip() for tok in value.split(",") if tok.strip()}


# Internal ref classifications that are surfaced to users in V0. Everything
# else (software-dependency) is suppressed from matching, federation, and
# rendering — ASVE V0 is agent-composition analysis; for general software
# dep scans, point users at osv-scanner / Trivy (see footer + README).
_AGENT_SCOPES: frozenset[str] = frozenset({"agent-component", "agent-dependency"})


def _filter_agent_scope_refs(refs: list[ComponentRef]) -> list[ComponentRef]:
    """Drop software-dependency refs before they reach matching/federation/rendering."""
    return [r for r in refs if r.scope in _AGENT_SCOPES]


def load_corpus(advisories_root: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(advisories_root.rglob("*.yaml"))]


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


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _finding_line(f: Finding) -> str:
    """Render a finding line for verbose output, including attribution suffix."""
    base = f"{_component_label(f.component)} → {f.advisory_id} ({f.confidence})"
    if f.attributed_to:
        return f"{base} via {f.attributed_to}"
    return base


def _federation_targets_lines(refs: list[ComponentRef]) -> list[str]:
    """Render the verbose pre-query summary for `--db asve,osv`.

    Two parts: the queried PURL list (what was actually sent) and a count
    of skipped refs bucketed by ecosystem (so users can see what wasn't
    queried and why — ASVE-native ecosystems and identity-only refs have
    no PURL; OSV.dev wouldn't have records for them).
    """
    queried = collect_target_purls(refs)
    lines: list[str] = []
    if queried:
        lines.append(f"federation: querying {len(queried)} PURL(s) on osv.dev")
        for p in queried:
            lines.append(f"  {p}")
    else:
        lines.append("federation: no queryable PURLs (no versioned, OSV-mappable refs)")
    skipped_by_eco: dict[str, int] = {}
    for r in refs:
        if is_queryable(r):
            continue
        eco = r.ecosystem or "<no-ecosystem>"
        skipped_by_eco[eco] = skipped_by_eco.get(eco, 0) + 1
    if skipped_by_eco:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(skipped_by_eco.items()))
        total = sum(skipped_by_eco.values())
        lines.append(f"federation: skipped {total} ref(s) without queryable PURL ({parts})")
    return lines


def _stamp_source(corpus: list[dict], source: str) -> None:
    """Set `database_specific.asve.source = <source>` on every advisory
    that doesn't already declare a source. Mutates corpus in place."""
    for a in corpus:
        if not isinstance(a, dict):
            continue
        ds = a.setdefault("database_specific", {})
        if not isinstance(ds, dict):
            continue
        asve_block = ds.setdefault("asve", {})
        if isinstance(asve_block, dict) and "source" not in asve_block:
            asve_block["source"] = source


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
    help="Project root whose .claude settings are layered into endpoint resolution.",
)
_advisories_option_required = click.option(
    "--advisories",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="ASVE advisories directory (YAML records).",
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
_db_option = click.option(
    "--db",
    "db",
    type=click.Choice(_DB_CHOICES),
    default="asve",
    show_default=True,
    help=(
        "Advisory database(s) to consult. `asve` (default) uses only the local "
        "ASVE corpus. `asve,osv` also queries OSV.dev for additional records "
        "covering emitted PURLs; network required, fails soft if unreachable."
    ),
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
def main(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
) -> None:
    """ASVE vulnerability scanner. Use `repo` or `endpoint` subcommands."""
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


def _apply_group_opts(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
) -> tuple[Path | None, str, bool, str, bool]:
    """Forward shared options placed before the subcommand name.

    When a user runs `asve-scan --fail-on none repo ...`, Click parses
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
    return sarif, fail_on, verbose, output_format, no_color


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
) -> None:
    """Dispatch to the chosen renderer and write to stdout."""
    if output_format == "github":
        rendered = render_github(findings)
    elif output_format == "json":
        rendered = render_json(findings, advisory_index, stats)
    else:
        rendered = render_text(
            findings, advisory_index, stats, use_color=use_color, verbose=verbose
        )
    if rendered:
        click.echo(rendered)


def _collect_corpus_sources(corpus: list[dict]) -> set[str]:
    """Pull `database_specific.asve.source` from every advisory in the corpus."""
    sources: set[str] = set()
    for a in corpus:
        if not isinstance(a, dict):
            continue
        ds = a.get("database_specific")
        if not isinstance(ds, dict):
            continue
        asve = ds.get("asve")
        if not isinstance(asve, dict):
            continue
        src = asve.get("source")
        if isinstance(src, str) and src:
            sources.add(src)
    return sources


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
@_advisories_option_required
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_db_option
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
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    db: str,
    include_gitignored: bool,
) -> None:
    """Scan supported agent-stack manifests committed in a repository.

    Reports declared composition only: project-host config under
    `.claude/*` (what Claude Code would load if run in this repo) and
    manifest-backed SDK config like a root `.mcp.json`. SDK-inline and
    code-defined agent composition (e.g., `Agent(tools=[...])`,
    `query({ mcpServers: ... })`) are out of V0 scope and not surfaced.
    """
    sarif, fail_on, verbose, output_format, no_color = _apply_group_opts(
        ctx, sarif, fail_on, verbose, output_format, no_color
    )
    backends = _parse_db_selection(db)
    federate_osv = "osv" in backends

    grouped, n_found = parse_repo_grouped(target, include_gitignored=include_gitignored)
    # Dedup across discovery paths — the same logical component can appear in
    # multiple groups (e.g., a plugin's .mcp.json walked both directly and
    # indirectly via plugin.json's string-path mcpServers). Verbose output
    # still shows raw `grouped` so users see what each manifest declared.
    all_refs = flatten_grouped(grouped)
    # V0: drop software-dependency refs (deps from non-plugin manifests).
    # ASVE is agent-composition analysis; deps belonging to general
    # software in the repo are out of scope and would mislead users into
    # thinking ASVE is a general SCA tool. See README for framing.
    refs = _filter_agent_scope_refs(all_refs)
    suppressed_software = len(all_refs) - len(refs)
    n_failed = n_found - len(grouped)
    corpus = load_corpus(advisories)
    _stamp_source(corpus, "asve.dev")
    if federate_osv:
        before_ids = {a.get("id") for a in corpus if isinstance(a, dict)}
        corpus, fed_warnings = augment_corpus(refs, corpus)
        for a in corpus:
            if isinstance(a, dict) and a.get("id") not in before_ids:
                _stamp_source([a], "osv.dev")
        for fw in fed_warnings:
            click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}
    parse_note = f" ({n_failed} failed to parse)" if n_failed else ""

    if verbose:
        click.echo(f"loaded {len(corpus)} advisory(ies) from {advisories}", err=True)
        if grouped:
            click.echo(
                f"scanned {n_found} manifest(s), {len(refs)} component(s){parse_note}:",
                err=True,
            )
            for path, group in grouped:
                kept = _filter_agent_scope_refs(group)
                click.echo(
                    f"  {_relative_to(path, target)} — {len(kept)} component(s)",
                    err=True,
                )
            if suppressed_software:
                click.echo(
                    f"  ({suppressed_software} software-dependency ref(s) suppressed; "
                    "use osv-scanner or Trivy for general software scans)",
                    err=True,
                )
        elif n_found:
            click.echo(f"found {n_found} manifest file(s) but none parsed successfully", err=True)
        else:
            click.echo(f"no manifests found under {target}", err=True)
        if federate_osv:
            for line in _federation_targets_lines(refs):
                click.echo(line, err=True)
            osv_count = sum(
                1
                for f in findings
                if (advisory_index.get(f.advisory_id) or {})
                .get("database_specific", {})
                .get("asve", {})
                .get("source")
                == "osv.dev"
            )
            click.echo(f"federation: osv.dev returned {osv_count} additional finding(s)", err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index)
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
@_advisories_option_required
@_sarif_option
@_fail_on_option
@_verbose_option
@_format_option
@_no_color_option
@_db_option
def endpoint(
    ctx: click.Context,
    config_dir: Path | None,
    project: Path | None,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    output_format: str,
    no_color: bool,
    db: str,
) -> None:
    """Scan the active agent stack installed on this endpoint."""
    sarif, fail_on, verbose, output_format, no_color = _apply_group_opts(
        ctx, sarif, fail_on, verbose, output_format, no_color
    )
    backends = _parse_db_selection(db)
    federate_osv = "osv" in backends
    config_dir = _resolve_endpoint_config_dir(config_dir)

    refs, warnings = parse_install(
        install_root=config_dir,
        project_root=project,
        mode="endpoint",
        include_transitive=True,
    )
    corpus = load_corpus(advisories)
    _stamp_source(corpus, "asve.dev")
    if federate_osv:
        before_ids = {a.get("id") for a in corpus if isinstance(a, dict)}
        corpus, fed_warnings = augment_corpus(refs, corpus)
        for a in corpus:
            if isinstance(a, dict) and a.get("id") not in before_ids:
                _stamp_source([a], "osv.dev")
        for fw in fed_warnings:
            click.echo(f"warning: {fw}", err=True)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}
    plugin_count = sum(1 for r in refs if r.ecosystem == "claude-plugin")

    if verbose:
        click.echo(f"loaded {len(corpus)} advisory(ies) from {advisories}", err=True)
        roots_note = f"config_dir={config_dir}"
        if project is not None:
            roots_note += f", project={project}"
        click.echo(f"detected {roots_note} (mode=endpoint)", err=True)
        for w in warnings:
            click.echo(f"  warning: {w}", err=True)
        tree = render_inventory_tree(
            refs,
            findings,
            use_color=_use_color(no_color, output_format),
            use_unicode=_use_unicode(no_color),
        )
        if tree:
            click.echo(tree, err=True)
        if federate_osv:
            for line in _federation_targets_lines(refs):
                click.echo(line, err=True)
            osv_count = sum(
                1
                for f in findings
                if (advisory_index.get(f.advisory_id) or {})
                .get("database_specific", {})
                .get("asve", {})
                .get("source")
                == "osv.dev"
            )
            click.echo(f"federation: osv.dev returned {osv_count} additional finding(s)", err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(f"  {_finding_line(f)}", err=True)

    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index)
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
    )
    _stderr_summary(findings, f"resolved {plugin_count} active plugin(s)", output_format)

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
