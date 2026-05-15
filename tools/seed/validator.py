"""Validate reviewable seed candidates before promotion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tools.promote import project_candidate_to_overlay

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "openaca.schema.json"


def validate_candidate(candidate: dict[str, Any]) -> list[str]:
    """Return structural validation errors for a seed candidate."""
    errors: list[str] = []
    metadata = candidate.get("_candidate")
    if not isinstance(metadata, dict):
        errors.append("_candidate: required review metadata block is missing")
    elif not metadata.get("matched_by"):
        errors.append("_candidate.matched_by: must list at least one discovery heuristic")

    try:
        overlay = project_candidate_to_overlay(candidate)
    except ValueError as exc:
        errors.append(str(exc))
        return errors

    errors.extend(_check_threat_kind_id_coupling(candidate))
    errors.extend(_check_no_empty_taxonomy_buckets(candidate))

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(overlay):
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema: {error.message} (at {path})")
    return errors


def _get_openaca_dict(record: dict[str, Any]) -> dict[str, Any]:
    db = record.get("database_specific")
    openaca = (db if isinstance(db, dict) else {}).get("openaca")
    return openaca if isinstance(openaca, dict) else {}


def _check_threat_kind_id_coupling(candidate: dict[str, Any]) -> list[str]:
    """threat_kind is only valid when id or an alias starts with MAL-."""
    openaca = _get_openaca_dict(candidate)
    if "threat_kind" not in openaca:
        return []
    record_id = candidate.get("id") or ""
    aliases = candidate.get("aliases") or []
    if isinstance(record_id, str) and record_id.startswith("MAL-"):
        return []
    if any(isinstance(a, str) and a.startswith("MAL-") for a in aliases):
        return []
    return [
        f"threat_kind set on non-MAL record {record_id or '<unknown id>'}; "
        "threat_kind is only valid on MAL-* ids or aliases"
    ]


def _check_no_empty_taxonomy_buckets(candidate: dict[str, Any]) -> list[str]:
    """Reject empty arrays/dicts under taxonomies; omit the key instead."""
    openaca = _get_openaca_dict(candidate)
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
