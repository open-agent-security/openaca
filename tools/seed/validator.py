"""Validate reviewable seed candidates before promotion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tools.promote import project_candidate_to_overlay

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schema" / "asve.schema.json"


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

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(overlay):
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema: {error.message} (at {path})")
    return errors


def _check_threat_kind_id_coupling(candidate: dict[str, Any]) -> list[str]:
    """threat_kind is only valid when id or an alias starts with MAL-."""
    asve = (candidate.get("database_specific") or {}).get("asve") or {}
    if "threat_kind" not in asve:
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
