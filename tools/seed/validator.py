"""Validate reviewable seed candidates before promotion."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator

from tools.lint import check_no_empty_taxonomy_buckets, check_threat_kind_id_coupling, load_schema
from tools.promote import project_candidate_to_overlay


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

    errors.extend(check_threat_kind_id_coupling(candidate))
    errors.extend(check_no_empty_taxonomy_buckets(candidate))

    schema = load_schema()
    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(overlay):
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema: {error.message} (at {path})")
    return errors
