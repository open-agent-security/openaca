"""Seed reviewable ASVE overlay candidates from OSV bulk dumps."""

from __future__ import annotations

import json
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Iterable

import click
import yaml

from tools.seed.validator import validate_candidate

REPO_ROOT = Path(__file__).resolve().parents[2]

_NAME_PATTERN = re.compile(r"(?:^|[/_\-@])mcp(?:[/_\-]|$)", re.IGNORECASE)
_SUMMARY_HINTS = ("model context protocol", "mcp server", "mcp client", " mcp ")

_IMPACT_KEYS = (
    "repo_read",
    "repo_write",
    "credential_exfiltration",
    "tool_hijack",
    "memory_poisoning",
    "pr_manipulation",
    "code_execution",
)

_CLASS_RULES: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (
        (
            "command injection",
            "command execution",
            "os command",
            "shell injection",
            "remote code execution",
            "rce",
            "arbitrary code",
            "code injection",
            "code execution",
            "execute arbitrary",
        ),
        {
            "agent_impact": {"code_execution": True, "tool_hijack": True},
            "taxonomies": {"owasp_agentic_top10": {"asi05"}},
        },
    ),
    (
        ("path traversal", "directory traversal", "arbitrary file read", "file disclosure"),
        {
            "agent_impact": {"repo_read": True},
            "taxonomies": {"owasp_agentic_top10": {"asi08"}},
        },
    ),
    (
        ("ssrf", "server-side request forgery"),
        {
            "agent_impact": {"credential_exfiltration": True},
            "taxonomies": {"owasp_agentic_top10": {"asi02"}},
        },
    ),
    (
        ("authentication bypass", "auth bypass", "missing authentication", "unauthenticated"),
        {
            "agent_impact": {"tool_hijack": True},
            "taxonomies": {"owasp_agentic_top10": {"asi02"}},
        },
    ),
    (
        ("prompt injection", "indirect prompt"),
        {
            "agent_impact": {"memory_poisoning": True, "tool_hijack": True},
            "taxonomies": {"owasp_agentic_top10": {"asi01"}},
        },
    ),
]


def _text(record: dict[str, Any]) -> str:
    return " ".join([str(record.get("summary") or ""), str(record.get("details") or "")]).lower()


def _package_names(record: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for affected in record.get("affected") or []:
        package = affected.get("package") or {}
        name = package.get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def discovery_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if any(_NAME_PATTERN.search(name) for name in _package_names(record)):
        reasons.append("package_name_mcp")
    text = _text(record)
    if any(hint in text for hint in _SUMMARY_HINTS):
        reasons.append("summary_mentions_mcp")
    return reasons


def iter_records(source: Path) -> Iterable[dict[str, Any]]:
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            for name in sorted(zf.namelist()):
                if not name.endswith(".json"):
                    continue
                try:
                    data = json.loads(zf.read(name))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(data, dict):
                    yield data
        return

    paths = [source] if source.is_file() else sorted(source.rglob("*.json"))
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            yield data


def _load_state(state: Path | None) -> tuple[str | None, set[str]]:
    if state is None or not state.exists():
        return None, set()
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, set()
    last_modified = data.get("last_modified")
    ts = last_modified if isinstance(last_modified, str) else None
    raw_ids = data.get("last_modified_ids")
    ids: set[str] = set(raw_ids) if isinstance(raw_ids, list) else set()
    return ts, ids


def _record_path(records_root: Path, row_id: str) -> Path:
    path = records_root / f"{row_id}.json"
    resolved_root = records_root.resolve()
    resolved_path = path.resolve()
    if resolved_path.parent != resolved_root and resolved_root not in resolved_path.parents:
        raise ValueError(f"unsafe modified_id.csv record id: {row_id!r}")
    return path


def _modified_record_zip_paths(records_root: Path, row_id: str) -> list[Path]:
    paths: list[Path] = []
    ecosystem, sep, _record_id = row_id.partition("/")
    if sep:
        paths.append(records_root / ecosystem / "all.zip")
    paths.append(records_root / "all.zip")
    return paths


def _load_modified_record(
    records_root: Path,
    row_id: str,
    zip_cache: dict[Path, zipfile.ZipFile],
) -> dict[str, Any] | None:
    path = _record_path(records_root, row_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = None
    if isinstance(data, dict):
        return data

    member = f"{Path(row_id).name}.json"
    for zip_path in _modified_record_zip_paths(records_root, row_id):
        if not zip_path.exists():
            continue
        try:
            if zip_path not in zip_cache:
                zip_cache[zip_path] = zipfile.ZipFile(zip_path)
            zf = zip_cache[zip_path]
            data = json.loads(zf.read(member))
        except (KeyError, json.JSONDecodeError, OSError, zipfile.BadZipFile):
            continue
        if isinstance(data, dict):
            return data
    return None


def iter_modified_records(
    modified_index: Path,
    records_root: Path,
    last_modified: str | None,
    last_modified_ids: set[str],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    zip_cache: dict[Path, zipfile.ZipFile] = {}
    try:
        for raw_line in modified_index.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            modified, sep, row_id = raw_line.partition(",")
            if not sep or not modified or not row_id:
                continue
            if last_modified is not None:
                if modified < last_modified:
                    break
                if modified == last_modified and row_id in last_modified_ids:
                    continue
            data = _load_modified_record(records_root, row_id, zip_cache)
            if data is None:
                continue
            yield modified, row_id, data
    finally:
        for zf in zip_cache.values():
            zf.close()


def _write_state(state: Path | None, newest_modified: str | None, newest_ids: set[str]) -> None:
    if state is None or newest_modified is None:
        return
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps(
            {"last_modified": newest_modified, "last_modified_ids": sorted(newest_ids)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _classify(record: dict[str, Any]) -> dict[str, Any]:
    impact = {key: False for key in _IMPACT_KEYS}
    taxonomies: dict[str, set[str]] = {"owasp_agentic_top10": set()}
    text = _text(record)

    for keywords, payload in _CLASS_RULES:
        if not any(keyword in text for keyword in keywords):
            continue
        for key, value in (payload.get("agent_impact") or {}).items():
            if value:
                impact[key] = True
        for family, values in (payload.get("taxonomies") or {}).items():
            taxonomies.setdefault(family, set()).update(values)

    if str(record.get("id") or "").startswith("MAL-"):
        impact["code_execution"] = True
        impact["credential_exfiltration"] = True
        threat_kind = "malicious_package"
    else:
        threat_kind = None

    taxonomy_lists = {key: sorted(value) for key, value in taxonomies.items() if value}
    if not taxonomy_lists:
        taxonomy_lists = {"owasp_agentic_top10": ["asi05"]}

    asve: dict[str, Any] = {
        "component_type": "mcp_server",
        "surfaces": ["tool_invocation", "stdio"],
        "agent_impact": impact,
        "taxonomies": taxonomy_lists,
        "evidence_level": "likely",
    }
    if threat_kind:
        asve["threat_kind"] = threat_kind
    return asve


def build_candidate(record: dict[str, Any], matched_by: list[str]) -> dict[str, Any]:
    rec_id = record.get("id")
    if not isinstance(rec_id, str) or not rec_id:
        raise ValueError("OSV record is missing id")

    aliases = [a for a in record.get("aliases") or [] if isinstance(a, str) and a != rec_id]
    candidate: dict[str, Any] = {
        "schema_version": record.get("schema_version") or "1.7.5",
        "id": rec_id,
        "modified": record.get("modified") or record.get("published") or "1970-01-01T00:00:00Z",
        "_candidate": {
            "review_status": "needs_review",
            "matched_by": matched_by,
            "package_names": _package_names(record),
        },
        "_evidence": [
            {"field": "summary", "quote": record.get("summary") or ""},
        ],
        "database_specific": {"asve": _classify(record)},
    }
    if aliases:
        candidate["aliases"] = aliases
    for key in ("summary", "details", "references", "affected"):
        if key in record:
            candidate[key] = record[key]
    return candidate


def _identity(record: dict[str, Any]) -> set[str]:
    ids = {record.get("id")}
    ids.update(a for a in record.get("aliases") or [] if isinstance(a, str))
    return {i for i in ids if isinstance(i, str)}


def _curated_keys(existing_overlays: Path) -> set[str]:
    keys: set[str] = set()
    if not existing_overlays.exists():
        return keys
    for path in existing_overlays.rglob("*.yaml"):
        try:
            overlay = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(overlay, dict):
            continue
        keys.update(_identity(overlay))
    return keys


@click.command()
@click.argument("source", required=False, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--modified-index",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="OSV modified_id.csv file. Reads only records newer than --state.",
)
@click.option(
    "--records-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Root directory containing JSON records referenced by --modified-index.",
)
@click.option(
    "--state",
    type=click.Path(dir_okay=False, path_type=Path),
    help="JSON state file containing last_modified for incremental seeding.",
)
@click.option(
    "--out",
    "out_dir",
    default=Path("candidates"),
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory for reviewable candidate YAML files.",
)
@click.option(
    "--existing",
    "existing_overlays",
    default=REPO_ROOT / "overlays",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Existing overlay corpus used for alias deduplication.",
)
@click.option("--dry-run", is_flag=True, help="Print candidates without writing files.")
def main(
    source: Path | None,
    modified_index: Path | None,
    records_root: Path | None,
    state: Path | None,
    out_dir: Path,
    existing_overlays: Path,
    dry_run: bool,
) -> None:
    """Generate deterministic review candidates from an OSV JSON directory or zip."""
    if modified_index is not None and records_root is None:
        raise click.UsageError("--records-root is required with --modified-index")
    if modified_index is None and source is None:
        raise click.UsageError("SOURCE is required unless --modified-index is provided")

    curated = _curated_keys(existing_overlays)
    scanned = matched = skipped = written = 0
    newest_modified: str | None = None
    newest_modified_ids: set[str] = set()

    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_resolved = out_dir.resolve()

    last_modified, last_modified_ids = _load_state(state)

    if modified_index is not None:
        assert records_root is not None
        records: Iterable[tuple[str | None, str | None, dict[str, Any]]] = iter_modified_records(
            modified_index, records_root, last_modified, last_modified_ids
        )
    else:
        assert source is not None
        records = ((None, None, record) for record in iter_records(source))

    for modified, row_id, record in records:
        scanned += 1
        if modified is not None:
            if newest_modified is None:
                newest_modified = modified
            if modified == newest_modified and row_id is not None:
                newest_modified_ids.add(row_id)
        matched_by = discovery_reasons(record)
        if not matched_by:
            continue
        matched += 1
        if _identity(record) & curated:
            skipped += 1
            continue

        candidate = build_candidate(record, matched_by)
        errors = validate_candidate(candidate)
        if errors:
            click.echo(f"{candidate.get('id')}: candidate validation failed", err=True)
            for error in errors:
                click.echo(f"  {error}", err=True)
            sys.exit(1)

        target = out_dir / f"{candidate['id']}.yaml"
        if target.resolve().parent != out_dir_resolved:
            click.echo(f"{candidate['id']!r}: unsafe candidate ID, skipping", err=True)
            continue
        if dry_run:
            click.echo(f"would write {target}: {candidate.get('summary', '')[:80]}")
        else:
            target.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
        written += 1
        curated.update(_identity(record))

    if newest_modified is not None and newest_modified == last_modified:
        newest_modified_ids |= last_modified_ids

    if not dry_run:
        _write_state(state, newest_modified, newest_modified_ids)

    click.echo(
        f"scanned {scanned} records, {matched} matched, "
        f"{skipped} already curated, {written} candidate{'s' if written != 1 else ''} "
        f"{'(dry-run)' if dry_run else 'written'}"
    )


if __name__ == "__main__":
    main()
