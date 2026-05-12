"""Resync ASVE corpus records from their upstream OSV/GHSA/CVE aliases.

For each ASVE record carrying `aliases:`, this tool fetches the first
resolvable alias from osv.dev and overwrites the editorial fields
(`severity`, `affected[].ranges`) with upstream's authoritative values.
Agent-overlay fields under `database_specific.asve.*` are preserved.
ASVE-native affected entries (ecosystems not present upstream, e.g.
`claude-plugin`, `claude-skill`) are also preserved.

Rationale: ASVE's wedge is the agent overlay, not parallel CVSS or fix-
range authority. Hand-authoring those fields at filing time produced
drift in the V0 corpus — ASVE-2026-0001 was filed with `fixed: 1.2.3`
and a custom v4 CRITICAL vector, while GHSA-3q26-f695-pp76 says
`fixed: 2.1.5` and v3.1 7.5 HIGH. Resync lets the corpus stay offline-
usable and self-contained without inventing facts that upstream owns.

Usage:
    uv run asve-resync advisories/         # resync all
    uv run asve-resync advisories/ --check # exit non-zero if any record drifts
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import click
import yaml

_OSV_VULN_URL = "https://api.osv.dev/v1/vulns/{id}"
_TIMEOUT_SECONDS = 15


def fetch_alias(alias_id: str) -> dict | None:
    """Fetch an OSV/GHSA/CVE record from osv.dev. Returns None on 404/network error.

    osv.dev resolves GHSA, CVE, and OSV identifiers under the same URL;
    a missing record returns 404. Network failures are non-fatal so a
    single bad alias doesn't abort the whole resync run.
    """
    url = _OSV_VULN_URL.format(id=alias_id)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


def _pkg_key(pkg: dict) -> tuple[str, str] | None:
    """Stable (ecosystem, name) key for matching affected entries across records."""
    if not isinstance(pkg, dict):
        return None
    eco = pkg.get("ecosystem")
    name = pkg.get("name")
    if not isinstance(eco, str) or not isinstance(name, str):
        return None
    return (eco, name)


def merge_upstream(asve: dict, upstream: dict) -> dict:
    """Return a copy of `asve` with severity and affected[].ranges replaced
    from `upstream`.

    - `severity:` is replaced wholesale; if upstream has none, severity is
      removed from the output (better to surface "unknown" than carry a
      stale hand-authored vector).
    - `affected[]` entries are matched by (ecosystem, name). For each
      ASVE entry with a matching upstream entry, the `ranges` field is
      overwritten. ASVE-only entries (no upstream match) are kept as-is —
      this preserves agent-stack pseudo-ecosystems like `claude-plugin`
      that upstream doesn't track.
    - Upstream-only entries (packages we didn't have) are NOT added; the
      ASVE record's package surface is the source of truth for which
      packages this advisory covers in our agent-context taxonomy.
    - `modified:` is bumped to today (UTC, ISO-8601).
    """
    out = copy.deepcopy(asve)
    upstream_severity = upstream.get("severity")
    if upstream_severity:
        out["severity"] = copy.deepcopy(upstream_severity)
    elif "severity" in out:
        del out["severity"]

    upstream_affected = upstream.get("affected") or []
    upstream_ranges_by_key: dict[tuple[str, str], list] = {}
    for entry in upstream_affected:
        if not isinstance(entry, dict):
            continue
        key = _pkg_key(entry.get("package") or {})
        if key is None:
            continue
        ranges = entry.get("ranges")
        if isinstance(ranges, list):
            upstream_ranges_by_key[key] = copy.deepcopy(ranges)

    for entry in out.get("affected") or []:
        if not isinstance(entry, dict):
            continue
        key = _pkg_key(entry.get("package") or {})
        if key is None:
            continue
        if key in upstream_ranges_by_key:
            entry["ranges"] = upstream_ranges_by_key[key]

    out["modified"] = _now_iso()
    return out


def _now_iso() -> str:
    """UTC ISO-8601 timestamp matching the OSV schema's modified format."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise click.ClickException(f"{path}: top-level must be a mapping")
    return data


def _dump_yaml(data: dict, path: Path) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def resync_record(asve: dict) -> tuple[dict | None, str | None]:
    """Return (resynced_record, alias_used) or (None, reason) when no resync occurred.

    Tries each alias in order; the first one that resolves to a record with
    either severity or affected[].ranges is used. Records without aliases
    are returned as (None, "no aliases") — hand-authored ASVE-only
    advisories (e.g., for `claude-plugin/*` ecosystems with no upstream
    counterpart) skip resync.
    """
    aliases = asve.get("aliases") or []
    if not aliases:
        return None, "no aliases"
    for alias in aliases:
        upstream = fetch_alias(alias)
        if not upstream:
            continue
        if not upstream.get("severity") and not upstream.get("affected"):
            continue
        return merge_upstream(asve, upstream), alias
    return None, "no aliases resolvable on osv.dev"


@click.command()
@click.argument(
    "advisories_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Exit non-zero if any record would change. Use as a CI gate to "
    "detect drift between the corpus and upstream.",
)
@click.option(
    "--id",
    "only_ids",
    multiple=True,
    help="Limit resync to these ASVE IDs (e.g., --id ASVE-2026-0001). "
    "Repeatable. Default: every YAML under advisories_dir.",
)
def main(advisories_dir: Path, check: bool, only_ids: tuple[str, ...]) -> None:
    """Resync ASVE corpus records from their upstream aliases on osv.dev."""
    changed_count = 0
    skipped_count = 0
    unchanged_count = 0
    drift_paths: list[Path] = []

    for path in sorted(advisories_dir.rglob("*.yaml")):
        try:
            record = _load_yaml(path)
        except click.ClickException:
            continue
        asve_id = record.get("id")
        if only_ids and asve_id not in only_ids:
            continue
        resynced, used = resync_record(record)
        if resynced is None:
            click.echo(f"{path.name}: skip ({used})", err=True)
            skipped_count += 1
            continue

        # Compare ignoring `modified` since that's always bumped — meaningful
        # change is only severity / affected.
        before = copy.deepcopy(record)
        before.pop("modified", None)
        after = copy.deepcopy(resynced)
        after.pop("modified", None)
        if before == after:
            click.echo(f"{path.name}: unchanged (alias {used})", err=True)
            unchanged_count += 1
            continue

        if check:
            click.echo(f"{path.name}: DRIFT vs alias {used}", err=True)
            drift_paths.append(path)
        else:
            _dump_yaml(resynced, path)
            click.echo(f"{path.name}: resynced from {used}", err=True)
        changed_count += 1

    if check and drift_paths:
        click.echo(
            f"{len(drift_paths)} record(s) drift from upstream; "
            "run `uv run asve-resync advisories/` to refresh.",
            err=True,
        )
        sys.exit(1)
    click.echo(
        f"summary: {changed_count} changed, {unchanged_count} unchanged, {skipped_count} skipped",
        err=True,
    )


if __name__ == "__main__":
    main()
