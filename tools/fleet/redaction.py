from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse


class RedactionError(Exception):
    pass


_HOME_PATH_RE = re.compile(
    r"(?i)(/Users/[^/\s]+|/home/[^/\s]+|/root(?:/|\b)|[a-z]:\\Users\\[^\\\s]+)"
)
_TOKEN_RE = re.compile(
    r"(?i)\b(ghp_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]+|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[abprs]-[A-Za-z0-9-]{20,}|ot_[A-Za-z0-9_]{20,})\b"
)

_POSTURE_EVIDENCE_KEYS = {
    "openaca-posture-insecure-transport": {"transport", "manifest_path"},
    "openaca-posture-mutable-install-reference": {"install_ref", "manifest_path"},
    "openaca-posture-api-endpoint-override": {"override_present", "manifest_path"},
    "openaca-posture-mcp-auto-approve": {
        "auto_approve",
        "approved_tool_count",
        "manifest_path",
    },
}


def validate_fleet_upload_payload(payload: dict[str, Any]) -> None:
    _validate_top_level(payload)
    _validate_bom_openaca_properties(payload.get("bom"))
    _validate_posture_findings(payload.get("posture_findings"))


def _validate_top_level(payload: dict[str, Any]) -> None:
    for key in ("asset_id", "source", "openaca_version", "target_locator", "content_hash"):
        value = payload.get(key)
        if isinstance(value, str):
            _validate_string(value, key)


def _validate_bom_openaca_properties(value: Any) -> None:
    for path, property_value in _iter_openaca_property_values(value, "bom"):
        if isinstance(property_value, str):
            _validate_string(property_value, path)


def _iter_openaca_property_values(value: Any, path: str) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, str) and name.startswith("openaca:") and "value" in value:
            yield f"{path}.value", value.get("value")
        for key, child in value.items():
            yield from _iter_openaca_property_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_openaca_property_values(child, f"{path}[{index}]")


def _validate_posture_findings(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise RedactionError("posture_findings must be an array")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RedactionError(f"posture_findings[{index}] must be an object")
        _validate_posture_finding(item, index)


def _validate_posture_finding(item: dict[str, Any], index: int) -> None:
    for key, value in item.items():
        path = f"posture_findings[{index}].{key}"
        if key == "evidence":
            continue
        _validate_value(value, path)

    rule_id = item.get("rule_id")
    evidence = item.get("evidence", {})
    if not isinstance(rule_id, str):
        raise RedactionError(f"posture_findings[{index}].rule_id must be a string")
    if not isinstance(evidence, dict):
        raise RedactionError(f"posture_findings[{index}].evidence must be an object")

    allowed = _POSTURE_EVIDENCE_KEYS.get(rule_id)
    if allowed is None:
        raise RedactionError(f"posture_findings[{index}].rule_id is not allowed")
    for key, value in evidence.items():
        if key not in allowed:
            raise RedactionError(f"posture_findings[{index}].evidence.{key} is not allowed")
        _validate_value(value, f"posture_findings[{index}].evidence.{key}")


def _validate_value(value: Any, path: str) -> None:
    if isinstance(value, str):
        _validate_string(value, path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_value(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_value(item, f"{path}.{key}")


def _validate_string(value: str, path: str) -> None:
    if _HOME_PATH_RE.search(value):
        raise RedactionError(f"{path} contains a local user path")
    if _TOKEN_RE.search(value):
        raise RedactionError(f"{path} contains a token-like value")
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.query:
        raise RedactionError(f"{path} contains a URL query string")
