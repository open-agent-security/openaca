"""End-to-end ASVE scan: parse → match → report (SARIF + annotations).

Invocation surface (used by the action.yml composite-action wrapper
and by humans via `uv run asve-scan`):

    asve-scan --target <repo> --advisories <dir> [--sarif <path>]
              [--fail-on high|any|none] [-v|--verbose]

Output:
- GitHub workflow annotations on stdout (`::error::` / `::warning::`)
  so PR reviewers see findings inline at the manifest line.
- Optional SARIF v2.1.0 written to `--sarif` path; the Action
  uploads this via `github/codeql-action/upload-sarif` so the
  code-scanning UI surfaces it as a Pull-request review.
- Exit code: 1 if any finding crosses the `--fail-on` threshold,
  0 otherwise. CI usually wires this to PR check status.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml

from tools.component_ref import ComponentRef
from tools.matcher import Finding, match
from tools.parsers import parse_repo_grouped
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


def emit_github_annotations(findings: list[Finding]) -> None:
    """Emit GitHub workflow annotations for each finding, one per line on stdout."""
    level_for = {"high": "error", "low": "warning", "unknown": "warning"}
    for f in findings:
        kind = level_for.get(f.confidence, "warning")
        file_param = _esc_param(str(f.component.source_manifest))
        title_param = _esc_param(f.advisory_id)
        message = _esc_data(f.reason or f.advisory_id)
        click.echo(f"::{kind} file={file_param},title={title_param}::{message}")


@click.command()
@click.option(
    "--target",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Repo to scan.",
)
@click.option(
    "--advisories",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="ASVE advisories directory (YAML records).",
)
@click.option(
    "--sarif",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write SARIF v2.1.0 to this path.",
)
@click.option(
    "--fail-on",
    type=click.Choice(["high", "any", "none"]),
    default="any",
    show_default=True,
    help="Exit non-zero when findings of this severity are present.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    default=False,
    help="Print the per-manifest component breakdown and matched components.",
)
def main(target: Path, advisories: Path, sarif: Path | None, fail_on: str, verbose: bool) -> None:
    """Scan TARGET for components matching ASVE advisories."""
    grouped, n_found = parse_repo_grouped(target)
    refs = [ref for _, group in grouped for ref in group]
    corpus = load_corpus(advisories)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}

    if verbose:
        click.echo(f"loaded {len(corpus)} advisory(ies) from {advisories}", err=True)
        if grouped:
            click.echo(f"scanned {len(grouped)} manifest(s), {len(refs)} component(s):", err=True)
            for path, group in grouped:
                click.echo(f"  {_relative_to(path, target)} — {len(group)} component(s)", err=True)
        elif n_found:
            click.echo(f"found {n_found} manifest file(s) but none parsed successfully", err=True)
        else:
            click.echo(f"no manifests found under {target}", err=True)
        if findings:
            click.echo(f"matched {len(findings)} finding(s):", err=True)
            for f in findings:
                click.echo(
                    f"  {_component_label(f.component)} → {f.advisory_id} ({f.confidence})",
                    err=True,
                )

    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index)
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    emit_github_annotations(findings)

    summary = f"scanned {len(grouped)} manifest(s), {len(refs)} component(s)"
    if not findings:
        if not grouped:
            if n_found:
                click.echo(
                    f"found {n_found} manifest file(s) but none parsed successfully", err=True
                )
            else:
                click.echo(f"no manifests found under {target}", err=True)
        else:
            click.echo(f"{summary}; no findings", err=True)
        sys.exit(0)

    high_count = sum(1 for f in findings if f.confidence == "high")
    click.echo(
        f"{summary}; {len(findings)} finding(s), {high_count} high-confidence",
        err=True,
    )

    if fail_on == "none":
        sys.exit(0)
    if fail_on == "high" and high_count == 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
