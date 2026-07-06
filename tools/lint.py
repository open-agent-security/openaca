"""OpenACA overlay linter."""

from __future__ import annotations

import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

import click
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from tools.cvss import is_valid_cvss

_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _check_date_time(value: object) -> bool:
    if isinstance(value, str):
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


@_FORMAT_CHECKER.checks("uri", raises=ValueError)
def _check_uri(value: object) -> bool:
    if isinstance(value, str) and not urllib.parse.urlparse(value).scheme:
        raise ValueError(f"not a valid URI: {value!r}")
    return True


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca.schema.json"
CAPABILITY_SCHEMA_PATH = REPO_ROOT / "schema" / "openaca-capability.schema.json"
# Recognized upstream ID families per ADR-0009 (overlays/<upstream-id>.yaml).
UPSTREAM_ID_RE = re.compile(
    r"^(GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|CVE-\d{4}-\d+|OSV-\d{4}-\d+|PYSEC-\d{4}-\d+|MAL-\d{4}-\d+)$"
)


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def find_overlays(target: Path) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix in {".yaml", ".yml"} else []
    return sorted(p for p in target.rglob("*.yaml"))


def check_path_consistency(overlay: dict, path: Path) -> list[str]:
    # Exempt files outside the overlays corpus tree (e.g., test fixtures).
    if "overlays" not in path.parts:
        return []
    overlay_id = overlay.get("id", "")
    expected_filename = f"{overlay_id}.yaml"
    errors: list[str] = []
    if path.name != expected_filename:
        errors.append(f"path: filename {path.name!r} should be {expected_filename!r}")
    return errors


def check_id_format(overlay: dict, path: Path) -> list[str]:
    if "overlays" not in path.parts:
        return []
    oid = overlay.get("id", "")
    if not isinstance(oid, str) or not UPSTREAM_ID_RE.match(oid):
        return [
            f"id: {oid!r} is not a recognized upstream ID format "
            "(GHSA-*, CVE-*, OSV-*, PYSEC-*, or MAL-*)"
        ]
    return []


def check_duplicate_id(
    overlay: dict,
    path: Path,
    duplicate_ids: set[str],
    id_to_first_path: dict[str, Path],
) -> list[str]:
    if "overlays" not in path.parts:
        return []
    oid = overlay.get("id", "")
    if oid in duplicate_ids:
        first = id_to_first_path.get(oid, path)
        return [f"id: overlay id {oid!r} also declared in {first}"]
    return []


def check_cvss(overlay: dict) -> list[str]:
    errors: list[str] = []
    for i, sev in enumerate(overlay.get("severity") or []):
        sev_type = sev.get("type")
        score = sev.get("score", "")
        if sev_type in {"CVSS_V3", "CVSS_V4"} and not is_valid_cvss(sev_type, score):
            label = sev_type.replace("CVSS_V", "v")
            errors.append(
                f"cvss: severity[{i}].score is not a valid CVSS {label} vector: {score!r}"
            )
    return errors


def _get_openaca_dict(record: dict) -> dict:
    db = record.get("database_specific")
    openaca = (db if isinstance(db, dict) else {}).get("openaca")
    return openaca if isinstance(openaca, dict) else {}


def check_threat_kind_id_coupling(overlay: dict) -> list[str]:
    """threat_kind valid only on MAL-* ids/aliases (mirrors validator.py)."""
    openaca = _get_openaca_dict(overlay)
    if "threat_kind" not in openaca:
        return []
    record_id = overlay.get("id") or ""
    aliases = overlay.get("aliases") or []
    if isinstance(record_id, str) and record_id.startswith("MAL-"):
        return []
    if any(isinstance(a, str) and a.startswith("MAL-") for a in aliases):
        return []
    return [
        f"threat_kind set on non-MAL record {record_id or '<unknown id>'}; "
        "threat_kind is only valid on MAL-* ids or aliases"
    ]


def check_no_empty_taxonomy_buckets(overlay: dict) -> list[str]:
    """Reject empty arrays/dicts under taxonomies (mirrors validator.py)."""
    openaca = _get_openaca_dict(overlay)
    taxonomies = openaca.get("taxonomies")
    if not isinstance(taxonomies, dict):
        return []
    errors: list[str] = []
    for key, value in taxonomies.items():
        if isinstance(value, (list, dict)) and len(value) == 0:
            kind = "array" if isinstance(value, list) else "object"
            errors.append(
                f"empty taxonomy bucket {key!r}; omit the key instead of emitting an empty {kind}"
            )
    return errors


def check_schema(overlay: dict, validator: Draft202012Validator) -> list[str]:
    errors = []
    for e in validator.iter_errors(overlay):
        path = "/".join(map(str, e.absolute_path)) or "<root>"
        errors.append(f"schema: {e.message} (at {path})")
        # When type-branching rejects exposure/config, surface the type
        # explicitly so contributors see why the record was rejected.
        if e.validator == "not" and isinstance(overlay, dict) and "type" in overlay:
            errors.append(f"schema: type '{overlay['type']}' is reserved; rejected in V0")
    return errors


def lint_capability_dir(target: Path) -> list[str]:
    """Lint curated capability records under TARGET, mirroring overlay lint.

    Returns a flat list of ``"<path>: <error>"`` strings (empty == clean).
    Discovers records with ``rglob("*.yaml")`` to match the Task-4 loader.
    """
    schema = json.loads(CAPABILITY_SCHEMA_PATH.read_text())
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    records = find_overlays(target)

    loaded: list[tuple[Path, dict | None, str | None]] = []
    identity_first: dict[str, Path] = {}
    coordinate_first: dict[str, Path] = {}
    duplicate_identities: dict[str, Path] = {}
    duplicate_coordinates: dict[str, Path] = {}
    for path in records:
        try:
            record = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            loaded.append((path, None, f"yaml: {e}"))
            continue
        loaded.append((path, record, None))
        if not isinstance(record, dict):
            continue
        identity = record.get("identity")
        coordinate = record.get("match_coordinate")
        if isinstance(coordinate, str):
            if coordinate in coordinate_first:
                duplicate_coordinates[coordinate] = coordinate_first[coordinate]
            else:
                coordinate_first[coordinate] = path
        elif isinstance(identity, str):
            if identity in identity_first:
                duplicate_identities[identity] = identity_first[identity]
            else:
                identity_first[identity] = path

    errors: list[str] = []
    for path, record, parse_error in loaded:
        if parse_error is not None or record is None:
            errors.append(f"{path}: {parse_error or 'failed to load'}")
            continue
        record_errors = check_schema(record, validator)
        for cap in record.get("capabilities") or []:
            if isinstance(cap, dict) and not (cap.get("evidence") or []):
                record_errors.append(
                    f"evidence: capability {cap.get('name', '<unknown>')!r} has no evidence"
                )
        coordinate = record.get("match_coordinate")
        identity = record.get("identity")
        if isinstance(coordinate, str) and coordinate in duplicate_coordinates:
            record_errors.append(
                f"match_coordinate: {coordinate!r} also declared in "
                f"{duplicate_coordinates[coordinate]}"
            )
        elif isinstance(identity, str) and identity in duplicate_identities:
            record_errors.append(
                f"identity: {identity!r} also declared in {duplicate_identities[identity]}"
            )
        errors.extend(f"{path}: {err}" for err in record_errors)
    return errors


def _is_capability_target(target: Path) -> bool:
    """Detect a capability corpus vs. an overlay corpus by record shape.

    Capability records key on ``identity`` + ``capabilities``; overlays key on
    ``id`` + ``database_specific``. Sniff the first parseable record so a
    single ``lint`` command dispatches to the right validator without a
    separate subcommand.
    """
    for path in find_overlays(target):
        try:
            record = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(record, dict):
            return "capabilities" in record and "id" not in record
    return False


@click.command()
@click.argument("target", type=click.Path(exists=True, path_type=Path))
def main(target: Path) -> None:
    """Lint OpenACA overlays (or a capability corpus) under TARGET."""
    if _is_capability_target(target):
        records = find_overlays(target)
        errors = lint_capability_dir(target)
        for err in errors:
            click.echo(err, err=True)
        if not records:
            click.echo(f"no capability YAML files found under {target}", err=True)
            sys.exit(0)
        if errors:
            click.echo(f"\n{len(errors)} capability lint error(s)", err=True)
            sys.exit(1)
        for path in records:
            click.echo(f"{path}: ok")
        return

    schema = load_schema()
    validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)
    overlays = find_overlays(target)

    if not overlays:
        click.echo(f"no overlay YAML files found under {target}", err=True)
        sys.exit(0)

    # Pass 1: load all overlays; detect cross-path duplicates.
    loaded: list[tuple[Path, dict | None, str | None]] = []
    id_to_first_path: dict[str, Path] = {}
    duplicate_ids: set[str] = set()
    for path in overlays:
        try:
            overlay = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            loaded.append((path, None, f"yaml: {e}"))
            continue
        loaded.append((path, overlay, None))
        if isinstance(overlay, dict) and isinstance(overlay.get("id"), str):
            oid = overlay["id"]
            if oid in id_to_first_path:
                duplicate_ids.add(oid)
            else:
                id_to_first_path[oid] = path

    # Pass 2: per-overlay checks.
    failed = 0
    for path, overlay, parse_error in loaded:
        if parse_error is not None or overlay is None:
            click.echo(f"{path}: {parse_error or 'failed to load'}", err=True)
            failed += 1
            continue
        errors = (
            check_schema(overlay, validator)
            + check_cvss(overlay)
            + check_id_format(overlay, path)
            + check_path_consistency(overlay, path)
            + check_duplicate_id(overlay, path, duplicate_ids, id_to_first_path)
            + check_threat_kind_id_coupling(overlay)
            + check_no_empty_taxonomy_buckets(overlay)
        )
        if errors:
            failed += 1
            for err in errors:
                click.echo(f"{path}: {err}", err=True)
        else:
            click.echo(f"{path}: ok")

    if failed:
        click.echo(f"\n{failed} of {len(overlays)} overlays failed", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
