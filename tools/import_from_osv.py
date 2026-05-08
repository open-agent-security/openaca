"""Generate an ASVE advisory skeleton from an upstream OSV record."""

from __future__ import annotations

import copy
import json
import urllib.request
from pathlib import Path

import click
import yaml


def osv_to_asve_skeleton(osv: dict, asve_id: str) -> dict:
    """Map an OSV record into an ASVE skeleton (TODOs for human-author fields)."""
    aliases: list[str] = []
    if osv.get("id"):
        aliases.append(osv["id"])
    aliases.extend(a for a in osv.get("aliases") or [] if a not in aliases)

    skeleton: dict = {
        "schema_version": osv.get("schema_version", "1.7.5"),
        "id": asve_id,
        "type": "vulnerability",
        "aliases": aliases,
        "summary": osv.get("summary", "TODO"),
        "details": osv.get("details", "TODO"),
        "published": osv.get("published", "TODO"),
        "modified": osv.get("modified", "TODO"),
        "affected": copy.deepcopy(osv.get("affected") or []),
        "references": copy.deepcopy(osv.get("references") or []),
        "database_specific": {
            "asve": {
                "component_type": "TODO",
                "surfaces": [],
                "agent_impact": {
                    "repo_read": False,
                    "repo_write": False,
                    "credential_exfiltration": False,
                    "tool_hijack": False,
                    "memory_poisoning": False,
                    "pr_manipulation": False,
                    "code_execution": False,
                },
                "owasp_agentic_top10": [],
                "evidence_level": "likely",
            }
        },
    }
    return skeleton


def fetch_osv(osv_id: str) -> dict:
    """Fetch a single OSV record from osv.dev."""
    url = f"https://api.osv.dev/v1/vulns/{osv_id}"
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - fixed scheme
        return json.loads(response.read())


@click.command()
@click.option("--osv-id", help="Fetch this OSV/GHSA/CVE ID from osv.dev.")
@click.option(
    "--osv-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read OSV JSON from this file instead of fetching.",
)
@click.option("--asve-id", required=True, help="Target ASVE-YYYY-NNNN identifier.")
@click.option(
    "--out",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="Write the ASVE YAML skeleton to this path.",
)
def main(osv_id: str | None, osv_file: Path | None, asve_id: str, out: Path) -> None:
    """Generate an ASVE advisory skeleton from an OSV record."""
    if not osv_id and not osv_file:
        raise click.UsageError("specify --osv-id or --osv-file")
    if osv_file:
        osv = json.loads(osv_file.read_text())
    else:
        assert osv_id is not None
        osv = fetch_osv(osv_id)
    skeleton = osv_to_asve_skeleton(osv, asve_id=asve_id)
    out.write_text(yaml.safe_dump(skeleton, sort_keys=False))
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
