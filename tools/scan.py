"""End-to-end ASVE scan: parse → match → report (SARIF + annotations).

Two modes via subcommands (per ADR-0006); a subcommand is required:

    asve-scan repo --target <repo> --advisories <dir> [...]
        Walks declared manifests under the target repo. Used by the
        GitHub Action and standalone CLI scans of code repositories.

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
from tools.sarif import to_sarif


def load_corpus(advisories_root: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(advisories_root.rglob("*.yaml"))]


def _esc_param(value: str) -> str:
    """Percent-encode a workflow command parameter value per GitHub docs."""
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _esc_data(value: str) -> str:
    """Percent-encode a workflow command data (message) value per GitHub docs."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


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


_BUNDLED_KINDS: tuple[tuple[str, str], ...] = (
    ("npm", "MCPs"),
    ("PyPI", "MCPs"),
    ("claude-skill", "skills"),
    ("claude-hook", "hooks"),
    ("claude-command", "commands"),
    ("claude-agent", "agents"),
)


def _bundled_breakdown(refs: list[ComponentRef], plugin_identity: str | None) -> str:
    """Summarize per-plugin bundled-component counts for verbose output.

    Excludes Tier-2 lockfile refs (those with extra["transitive"] set);
    they are surfaced separately by _tier2_coverage_lines."""
    if plugin_identity is None:
        return "0 bundled"
    counts: dict[str, int] = {label: 0 for _, label in _BUNDLED_KINDS}
    for r in refs:
        if r.attributed_to != plugin_identity:
            continue
        # Skip Tier-2 lockfile refs; they're not bundled components.
        if r.extra.get("transitive") is not None:
            continue
        for eco, label in _BUNDLED_KINDS:
            if r.ecosystem == eco:
                counts[label] += 1
                break
    # MCPs span npm + PyPI; sum already happens via shared label.
    seen_labels = list(dict.fromkeys(label for _, label in _BUNDLED_KINDS))
    parts = [f"{counts[label]} bundled {label}" for label in seen_labels]
    return ", ".join(parts)


def _tier2_coverage_lines(refs: list[ComponentRef], plugin_identity: str | None) -> list[str]:
    """Per-plugin Tier-2 coverage: one line per ecosystem covered.

    Format:
      npm: package-lock.json (transitive, 247 packages)
      PyPI: package.json (direct only, 8 packages)
    """
    if plugin_identity is None:
        return []
    by_eco: dict[str, list[ComponentRef]] = {}
    for r in refs:
        if r.attributed_to != plugin_identity:
            continue
        if r.ecosystem not in {"npm", "PyPI"}:
            continue
        if r.extra.get("transitive") is None:
            continue
        by_eco.setdefault(r.ecosystem or "", []).append(r)
    out: list[str] = []
    for eco, ecorefs in sorted(by_eco.items()):
        is_transitive = any(r.extra.get("transitive") is True for r in ecorefs)
        if is_transitive:
            source = "package-lock.json" if eco == "npm" else "uv.lock"
            out.append(f"{eco}: {source} (transitive, {len(ecorefs)} packages)")
        else:
            source = "package.json" if eco == "npm" else "pyproject.toml"
            out.append(f"{eco}: {source} (direct only, {len(ecorefs)} packages)")
    return out


def _bare_breakdown(refs: list[ComponentRef]) -> str:
    """Summarize bare-component counts (no attribution) for verbose output."""
    counts: dict[str, int] = {}
    for r in refs:
        if r.attributed_to is not None:
            continue
        if r.ecosystem in {"npm", "PyPI"}:
            counts["MCPs"] = counts.get("MCPs", 0) + 1
        elif r.ecosystem == "claude-skill":
            counts["skills"] = counts.get("skills", 0) + 1
        elif r.ecosystem == "claude-hook":
            counts["hooks"] = counts.get("hooks", 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{n} {label}" for label, n in counts.items())


def _bare_listing(refs: list[ComponentRef]) -> list[str]:
    """Per-component identity strings for the bare (un-attributed) inventory.

    Mirrors the per-plugin breakdown: after the "bare components: 5 skills"
    summary line, each bare skill / hook / MCP gets its own indented line
    so users can see exactly what was inventoried. Sorted alphabetically
    within each ecosystem for deterministic output.
    """
    skills: list[str] = []
    hooks: list[str] = []
    mcps: list[str] = []
    for r in refs:
        if r.attributed_to is not None:
            continue
        if r.ecosystem == "claude-skill":
            skills.append(r.component_identity or f"claude-skill/{r.name}")
        elif r.ecosystem == "claude-hook":
            hooks.append(r.component_identity or "claude-hook/?")
        elif r.ecosystem in {"npm", "PyPI"}:
            mcps.append(r.purl or _component_label(r))
    return sorted(skills) + sorted(hooks) + sorted(mcps)


def _federation_targets_lines(refs: list[ComponentRef]) -> list[str]:
    """Render the verbose pre-query summary for `--federate-osv`.

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


def emit_github_annotations(findings: list[Finding]) -> None:
    """Emit GitHub workflow annotations for each finding, one per line on stdout."""
    level_for = {"high": "error", "low": "warning", "unknown": "warning"}
    for f in findings:
        kind = level_for.get(f.confidence, "warning")
        file_param = _esc_param(str(f.component.source_manifest))
        title_param = _esc_param(f.advisory_id)
        message = f.reason or f.advisory_id
        if f.attributed_to:
            message = f"{message} (via {f.attributed_to})"
        click.echo(f"::{kind} file={file_param},title={title_param}::{_esc_data(message)}")


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


# Group-level shared options (sarif / fail-on / verbose) can be placed BEFORE
# the subcommand name as a convenience; `_apply_group_opts` forwards them to
# the chosen subcommand. A subcommand is required — there is no
# no-subcommand fallback.
@click.group()
@click.pass_context
@_sarif_option
@_fail_on_option
@_verbose_option
def main(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
) -> None:
    """ASVE vulnerability scanner. Use `repo` or `endpoint` subcommands."""
    ctx.ensure_object(dict)
    ctx.obj["sarif"] = sarif
    ctx.obj["fail_on"] = fail_on
    ctx.obj["verbose"] = verbose


def _apply_group_opts(
    ctx: click.Context,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
) -> tuple[Path | None, str, bool]:
    """Forward shared options placed before the subcommand name.

    When a user runs `asve-scan --fail-on none repo ...`, Click parses
    --fail-on at the group level and the subcommand sees its own default.
    Read the group's ctx.obj and apply any option the subcommand didn't
    explicitly receive from the command line.
    """
    obj = (ctx.parent.obj if ctx.parent else None) or {}
    if ctx.get_parameter_source("sarif") == ParameterSource.DEFAULT:
        sarif = obj.get("sarif", sarif)
    if ctx.get_parameter_source("fail_on") == ParameterSource.DEFAULT:
        fail_on = obj.get("fail_on", fail_on)
    if ctx.get_parameter_source("verbose") == ParameterSource.DEFAULT:
        verbose = obj.get("verbose", verbose)
    return sarif, fail_on, verbose


@main.command()
@click.pass_context
@_target_option_required
@_advisories_option_required
@_sarif_option
@_fail_on_option
@_verbose_option
@click.option(
    "--federate-osv",
    is_flag=True,
    default=False,
    help="Query OSV.dev for additional vulnerability records.",
)
def repo(
    ctx: click.Context,
    target: Path,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    federate_osv: bool,
) -> None:
    """Scan a code repository's declared manifests."""
    sarif, fail_on, verbose = _apply_group_opts(ctx, sarif, fail_on, verbose)
    grouped, n_found = parse_repo_grouped(target)
    # Dedup across discovery paths — the same logical component can appear in
    # multiple groups (e.g., a plugin's .mcp.json walked both directly and
    # indirectly via plugin.json's string-path mcpServers). Verbose output
    # still shows raw `grouped` so users see what each manifest declared.
    refs = flatten_grouped(grouped)
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
                click.echo(
                    f"  {_relative_to(path, target)} — {len(group)} component(s)",
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

    emit_github_annotations(findings)

    summary = f"scanned {n_found} manifest(s), {len(refs)} component(s){parse_note}"
    if not findings:
        if not grouped:
            if n_found:
                click.echo(
                    f"found {n_found} manifest file(s) but none parsed successfully",
                    err=True,
                )
            else:
                click.echo(f"no manifests found under {target}", err=True)
        else:
            click.echo(f"{summary}; no findings", err=True)
    else:
        high_count = sum(1 for f in findings if f.confidence == "high")
        click.echo(
            f"{summary}; {len(findings)} finding(s), {high_count} high-confidence",
            err=True,
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
@click.option(
    "--exclude-transitive",
    is_flag=True,
    default=False,
    help="Skip Tier-2 dependency scanning (lockfiles + manifest fallback). "
    "Tier-1 agent-stack inventory still emitted.",
)
@click.option(
    "--federate-osv",
    is_flag=True,
    default=False,
    help="Query OSV.dev for additional vulnerability records covering "
    "emitted PURLs. Augments the local corpus with osv.dev-sourced "
    "findings. Network required; fails soft if OSV.dev is unreachable.",
)
def endpoint(
    ctx: click.Context,
    config_dir: Path | None,
    project: Path | None,
    advisories: Path,
    sarif: Path | None,
    fail_on: str,
    verbose: bool,
    exclude_transitive: bool,
    federate_osv: bool,
) -> None:
    """Scan the active agent stack installed on this endpoint."""
    sarif, fail_on, verbose = _apply_group_opts(ctx, sarif, fail_on, verbose)
    config_dir = _resolve_endpoint_config_dir(config_dir)

    refs, warnings = parse_install(
        install_root=config_dir,
        project_root=project,
        mode="endpoint",
        include_transitive=not exclude_transitive,
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
        click.echo(f"resolved {plugin_count} active plugin(s):", err=True)
        for r in refs:
            if r.ecosystem == "claude-plugin":
                sha = r.extra.get("gitCommitSha")
                sha_note = f" (sha: {sha[:8]})" if isinstance(sha, str) and sha else ""
                bundled = _bundled_breakdown(refs, r.component_identity)
                scope_str = r.extra.get("scope")
                click.echo(
                    f"  {r.component_identity}{sha_note} [scope={scope_str}] → {bundled}",
                    err=True,
                )
                for line in _tier2_coverage_lines(refs, r.component_identity):
                    click.echo(f"    {line}", err=True)
        bare_summary = _bare_breakdown(refs)
        if bare_summary:
            click.echo(f"bare components: {bare_summary}", err=True)
            for line in _bare_listing(refs):
                click.echo(f"  {line}", err=True)
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

    emit_github_annotations(findings)

    summary = f"resolved {plugin_count} active plugin(s)"
    if not findings:
        click.echo(f"{summary}; no findings", err=True)
    else:
        high_count = sum(1 for f in findings if f.confidence == "high")
        click.echo(
            f"{summary}; {len(findings)} finding(s), {high_count} high-confidence",
            err=True,
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
        resolved = Path(configured).expanduser()
        if not resolved.is_dir():
            raise click.UsageError(
                f"CLAUDE_CONFIG_DIR={configured!r} does not exist or is not a directory"
            )
        return resolved
    return Path.home() / ".claude"


if __name__ == "__main__":
    main()
