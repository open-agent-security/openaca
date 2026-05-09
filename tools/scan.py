"""End-to-end ASVE scan: parse → match → report (SARIF + annotations).

Invocation surface (used by the action.yml composite-action wrapper
and by humans via `uv run asve-scan`):

    asve-scan --target <repo> --advisories <dir> [--sarif <path>]
              [--fail-on high|any|none]

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

from tools.matcher import Finding, match
from tools.parsers import parse_repo
from tools.sarif import to_sarif


def load_corpus(advisories_root: Path) -> list[dict]:
    return [yaml.safe_load(p.read_text()) for p in sorted(advisories_root.rglob("*.yaml"))]


def emit_github_annotations(findings: list[Finding]) -> None:
    """Emit GitHub workflow annotations for each finding, one per line on stdout."""
    level_for = {"high": "error", "low": "warning", "unknown": "warning"}
    for f in findings:
        kind = level_for.get(f.confidence, "warning")
        click.echo(
            f"::{kind} file={f.component.source_manifest},title={f.advisory_id}::"
            f"{f.reason or f.advisory_id}"
        )


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
def main(target: Path, advisories: Path, sarif: Path | None, fail_on: str) -> None:
    """Scan TARGET for components matching ASVE advisories."""
    refs = parse_repo(target)
    corpus = load_corpus(advisories)
    findings = match(refs, corpus)

    advisory_index = {a["id"]: a for a in corpus}

    if sarif is not None:
        sarif_doc = to_sarif(findings, advisory_index)
        sarif.write_text(json.dumps(sarif_doc, indent=2) + "\n", encoding="utf-8")
        click.echo(f"sarif: wrote {sarif}", err=True)

    emit_github_annotations(findings)

    if not findings:
        click.echo("no findings", err=True)
        sys.exit(0)

    high_count = sum(1 for f in findings if f.confidence == "high")
    click.echo(
        f"{len(findings)} finding(s); {high_count} high-confidence",
        err=True,
    )

    if fail_on == "none":
        sys.exit(0)
    if fail_on == "high" and high_count == 0:
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
